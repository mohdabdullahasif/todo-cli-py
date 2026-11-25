"""Domain models and value parsing helpers for the Todo task manager."""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any

from todo.exceptions import ValidationError


class Priority(str, Enum):
    """Task priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def parse(cls, value: str | Priority) -> Priority:
        """Parse a priority value case-insensitively."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValidationError(f"Invalid priority: {value!r}. Expected low, medium, or high.")
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValidationError(f"Invalid priority {value!r}. Expected one of: low, medium, high.")

    @property
    def rank(self) -> int:
        """Numeric sort rank where smaller means higher urgency."""
        return _PRIORITY_RANK[self]


_PRIORITY_RANK: dict[Priority, int] = {
    Priority.HIGH: 0,
    Priority.MEDIUM: 1,
    Priority.LOW: 2,
}


class Recurrence(str, Enum):
    """Supported recurrence cadences for repeating tasks."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    @classmethod
    def parse(cls, value: str | Recurrence | None) -> Recurrence | None:
        """Parse an optional recurrence cadence case-insensitively."""
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValidationError(
                f"Invalid recurrence: {value!r}. Expected daily, weekly, or monthly."
            )
        normalized = value.strip().lower()
        if not normalized:
            return None
        for member in cls:
            if member.value == normalized:
                return member
        raise ValidationError(
            f"Invalid recurrence {value!r}. Expected one of: daily, weekly, monthly."
        )

    def next_due(self, base_date: date) -> date:
        """Calculate the next due date starting from base_date."""
        if self is Recurrence.DAILY:
            return base_date + timedelta(days=1)
        if self is Recurrence.WEEKLY:
            return base_date + timedelta(weeks=1)
        # Advance one calendar month while clamping end-of-month dates (e.g. Jan 31 -> Feb 28/29).
        year = base_date.year + (base_date.month // 12)
        month = (base_date.month % 12) + 1
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(base_date.day, last_day))


def normalize_tags(tags: Iterable[str] | None) -> list[str]:
    """Normalize, split comma-separated values, lowercase, and deduplicate tags."""
    if tags is None:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for raw_item in tags:
        if not isinstance(raw_item, str):
            raise ValidationError(f"Tag must be a string, got {type(raw_item).__name__}.")
        for part in raw_item.split(","):
            cleaned = part.strip().lower()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
    return normalized


def parse_due_date(value: date | datetime | str | None) -> date | None:
    """Parse an optional ISO-8601 date string or date instance."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValidationError(f"Invalid due date: {value!r}. Expected YYYY-MM-DD.")
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValidationError(
            f"Invalid due date {value!r}. Expected ISO format YYYY-MM-DD."
        ) from exc


def parse_datetime(value: datetime | str | None) -> datetime:
    """Parse an ISO-8601 datetime string or return a timezone-aware UTC datetime."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        raise ValidationError(f"Invalid timestamp: {value!r}.")
    cleaned = value.strip()
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValidationError(f"Invalid ISO timestamp: {value!r}.") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def validate_title(title: str) -> str:
    """Validate and strip a task title."""
    if not isinstance(title, str):
        raise ValidationError("Task title must be a string.")
    cleaned = title.strip()
    if not cleaned:
        raise ValidationError("Task title cannot be empty.")
    return cleaned


@dataclass(slots=True)
class Task:
    """Core representation of a to-do item."""

    id: int
    title: str
    done: bool = False
    due: date | None = None
    priority: Priority = Priority.MEDIUM
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    recurrence: Recurrence | None = None

    def copy(self) -> Task:
        """Return a defensive copy of this task."""
        return replace(self, tags=list(self.tags))

    def is_overdue(self, as_of: date | None = None) -> bool:
        """Return True when the task is pending and past its due date."""
        if self.done or self.due is None:
            return False
        return self.due < (as_of or date.today())

    def is_due_today(self, as_of: date | None = None) -> bool:
        """Return True when the task is pending and due on the reference date."""
        if self.done or self.due is None:
            return False
        return self.due == (as_of or date.today())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the task into a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "done": self.done,
            "due": self.due.isoformat() if self.due is not None else None,
            "priority": self.priority.value,
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at is not None else None,
            "recurrence": self.recurrence.value if self.recurrence is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, fallback_id: int | None = None) -> Task:
        """Deserialize a task from a mapping, raising ValidationError on invalid fields."""
        if not isinstance(data, Mapping):
            raise ValidationError("Task entry must be a JSON object.")

        raw_id = data.get("id", fallback_id)
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            raise ValidationError(f"Task 'id' must be a positive integer, got {raw_id!r}.")

        title = validate_title(data.get("title", ""))

        raw_done = data.get("done", False)
        if not isinstance(raw_done, bool):
            raise ValidationError(f"Task 'done' must be a boolean, got {raw_done!r}.")

        due = parse_due_date(data.get("due"))
        priority = Priority.parse(data.get("priority", Priority.MEDIUM.value))

        raw_tags = data.get("tags", [])
        if raw_tags is None:
            tags: list[str] = []
        elif isinstance(raw_tags, list):
            tags = normalize_tags(raw_tags)
        else:
            raise ValidationError("Task 'tags' must be a list of strings.")

        raw_created = data.get("created_at")
        created_at = parse_datetime(raw_created) if raw_created is not None else datetime.now(timezone.utc)

        raw_completed = data.get("completed_at")
        completed_at = parse_datetime(raw_completed) if raw_completed is not None else None

        recurrence = Recurrence.parse(data.get("recurrence"))

        return cls(
            id=raw_id,
            title=title,
            done=raw_done,
            due=due,
            priority=priority,
            tags=tags,
            created_at=created_at,
            completed_at=completed_at,
            recurrence=recurrence,
        )


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """Outcome of completing a task, including any spawned recurring follow-up task."""

    completed_task: Task
    next_task: Task | None = None


@dataclass(frozen=True, slots=True)
class ReminderReport:
    """Categorized view of pending tasks with due dates."""

    as_of: date
    overdue: list[Task]
    due_today: list[Task]
    upcoming: list[Task]

    @property
    def total_count(self) -> int:
        """Total number of tasks across all reminder buckets."""
        return len(self.overdue) + len(self.due_today) + len(self.upcoming)
