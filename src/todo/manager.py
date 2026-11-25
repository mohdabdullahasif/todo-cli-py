"""Core TaskManager business logic."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

from todo.exceptions import NothingToUndoError, TaskNotFoundError, ValidationError
from todo.models import (
    CompletionResult,
    Priority,
    Recurrence,
    ReminderReport,
    Task,
    normalize_tags,
    parse_due_date,
    validate_title,
)
from todo.storage import HistoryEntry, Storage

logger = logging.getLogger("todo.manager")

_UNSET: Any = object()
_VALID_SORT_FIELDS = {"id", "due", "priority", "created"}


class TaskManager:
    """High-level task operations independent of storage backend or UI."""

    def __init__(self, storage: Storage, *, undo_limit: int = 20) -> None:
        self.storage = storage
        self.undo_limit = undo_limit

    def _record_undo(self, description: str) -> None:
        tasks, next_id = self.storage.export_state()
        self.storage.push_history(
            HistoryEntry(description=description, next_id=next_id, tasks=tasks),
            max_entries=self.undo_limit,
        )

    def add(
        self,
        title: str,
        *,
        due: date | datetime | str | None = None,
        priority: Priority | str = Priority.MEDIUM,
        tags: Iterable[str] | None = None,
        recurrence: Recurrence | str | None = None,
    ) -> Task:
        """Create and persist a new task."""
        cleaned_title = validate_title(title)
        parsed_due = parse_due_date(due)
        parsed_priority = Priority.parse(priority)
        parsed_tags = normalize_tags(tags)
        parsed_recurrence = Recurrence.parse(recurrence)

        self._record_undo(f"add task '{cleaned_title}'")
        new_task = Task(
            id=0,
            title=cleaned_title,
            done=False,
            due=parsed_due,
            priority=parsed_priority,
            tags=parsed_tags,
            created_at=datetime.now(timezone.utc),
            completed_at=None,
            recurrence=parsed_recurrence,
        )
        saved = self.storage.add_task(new_task)
        logger.info("Added task #%d (%s)", saved.id, saved.title)
        return saved

    def get(self, task_id: int) -> Task:
        """Retrieve a task by ID or raise TaskNotFoundError."""
        task = self.storage.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def list(
        self,
        *,
        done: bool | None = None,
        tag: str | None = None,
        priority: Priority | str | None = None,
        search: str | None = None,
        sort_by: str = "id",
        descending: bool = False,
        overdue_only: bool = False,
        as_of: date | None = None,
    ) -> list[Task]:
        """List tasks matching the given filters and sort order."""
        normalized_sort = sort_by.strip().lower()
        if normalized_sort not in _VALID_SORT_FIELDS:
            valid = ", ".join(sorted(_VALID_SORT_FIELDS))
            raise ValidationError(f"Invalid sort field {sort_by!r}. Expected one of: {valid}.")

        parsed_priority = Priority.parse(priority) if priority is not None else None
        normalized_tag = tag.strip().lower() if tag and tag.strip() else None
        normalized_search = search.strip().lower() if search and search.strip() else None
        ref_date = as_of or date.today()

        tasks = self.storage.list_tasks()
        filtered: list[Task] = []
        for task in tasks:
            if done is not None and task.done is not done:
                continue
            if normalized_tag is not None and normalized_tag not in task.tags:
                continue
            if parsed_priority is not None and task.priority is not parsed_priority:
                continue
            if overdue_only and not task.is_overdue(as_of=ref_date):
                continue
            if normalized_search is not None:
                in_title = normalized_search in task.title.lower()
                in_tags = any(normalized_search in t for t in task.tags)
                if not (in_title or in_tags):
                    continue
            filtered.append(task)

        return self._sort_tasks(filtered, sort_by=normalized_sort, descending=descending)

    @staticmethod
    def _sort_tasks(tasks: list[Task], *, sort_by: str, descending: bool) -> list[Task]:
        if sort_by == "due":
            with_due = [t for t in tasks if t.due is not None]
            without_due = [t for t in tasks if t.due is None]
            with_due.sort(key=lambda t: (t.due, t.priority.rank, t.id), reverse=descending)
            without_due.sort(key=lambda t: (t.priority.rank, t.id))
            return with_due + without_due
        if sort_by == "priority":
            return sorted(
                tasks,
                key=lambda t: (
                    -t.priority.rank if descending else t.priority.rank,
                    t.due is None,
                    t.due or date.max,
                    t.id,
                ),
            )
        if sort_by == "created":
            return sorted(tasks, key=lambda t: (t.created_at, t.id), reverse=descending)
        return sorted(tasks, key=lambda t: t.id, reverse=descending)

    def complete_detailed(
        self,
        task_id: int,
        *,
        spawn_recurring: bool = True,
        as_of: date | None = None,
    ) -> CompletionResult:
        """Mark a task done and create the next occurrence if recurring."""
        task = self.get(task_id)
        if task.done:
            return CompletionResult(completed_task=task, next_task=None)

        self._record_undo(f"complete task #{task_id}")
        task.done = True
        task.completed_at = datetime.now(timezone.utc)
        completed = self.storage.update_task(task)
        logger.info("Completed task #%d", completed.id)

        next_task: Task | None = None
        if spawn_recurring and completed.recurrence is not None:
            base = completed.due or as_of or date.today()
            next_due = completed.recurrence.next_due(base)
            next_task = self.storage.add_task(
                Task(
                    id=0,
                    title=completed.title,
                    done=False,
                    due=next_due,
                    priority=completed.priority,
                    tags=list(completed.tags),
                    created_at=datetime.now(timezone.utc),
                    completed_at=None,
                    recurrence=completed.recurrence,
                )
            )
            logger.info(
                "Scheduled next %s occurrence #%d due %s",
                completed.recurrence.value,
                next_task.id,
                next_due.isoformat(),
            )

        return CompletionResult(completed_task=completed, next_task=next_task)

    def complete(self, task_id: int, *, spawn_recurring: bool = True) -> Task:
        """Mark a task as completed and return the completed Task."""
        return self.complete_detailed(task_id, spawn_recurring=spawn_recurring).completed_task

    def reopen(self, task_id: int) -> Task:
        """Mark a completed task back to pending."""
        task = self.get(task_id)
        if not task.done:
            return task
        self._record_undo(f"reopen task #{task_id}")
        task.done = False
        task.completed_at = None
        updated = self.storage.update_task(task)
        logger.info("Reopened task #%d", updated.id)
        return updated

    def update(
        self,
        task_id: int,
        *,
        title: str | None = None,
        due: date | datetime | str | None = _UNSET,
        priority: Priority | str | None = None,
        tags: Iterable[str] | None = None,
        recurrence: Recurrence | str | None = _UNSET,
        done: bool | None = None,
    ) -> Task:
        """Update selected fields of an existing task."""
        task = self.get(task_id)
        new_title = validate_title(title) if title is not None else task.title
        new_due = parse_due_date(due) if due is not _UNSET else task.due
        new_priority = Priority.parse(priority) if priority is not None else task.priority
        new_tags = normalize_tags(tags) if tags is not None else list(task.tags)
        new_recurrence = Recurrence.parse(recurrence) if recurrence is not _UNSET else task.recurrence
        new_done = task.done if done is None else done
        new_completed_at = task.completed_at
        if done is True and not task.done:
            new_completed_at = datetime.now(timezone.utc)
        elif done is False:
            new_completed_at = None

        self._record_undo(f"update task #{task_id}")
        updated_task = Task(
            id=task.id,
            title=new_title,
            done=new_done,
            due=new_due,
            priority=new_priority,
            tags=new_tags,
            created_at=task.created_at,
            completed_at=new_completed_at,
            recurrence=new_recurrence,
        )
        saved = self.storage.update_task(updated_task)
        logger.info("Updated task #%d", saved.id)
        return saved

    def delete(self, task_id: int) -> Task:
        """Delete a task by ID and return the deleted Task."""
        existing = self.get(task_id)
        self._record_undo(f"delete task #{task_id} '{existing.title}'")
        removed = self.storage.delete_task(task_id)
        logger.info("Deleted task #%d", removed.id)
        return removed

    def clear_completed(self) -> list[Task]:
        """Delete all completed tasks and return the removed tasks."""
        tasks, next_id = self.storage.export_state()
        completed = [t for t in tasks if t.done]
        if not completed:
            return []
        self._record_undo(f"clear {len(completed)} completed task(s)")
        remaining = [t for t in tasks if not t.done]
        self.storage.replace_state(remaining, next_id)
        logger.info("Cleared %d completed tasks", len(completed))
        return completed

    def undo(self) -> str:
        """Revert the most recent mutation and return a description of what was undone."""
        entry = self.storage.pop_history()
        if entry is None:
            raise NothingToUndoError()
        self.storage.replace_state(entry.tasks, entry.next_id)
        logger.info("Undid: %s", entry.description)
        return entry.description

    def reminders(self, *, within_days: int = 1, as_of: date | None = None) -> ReminderReport:
        """Return overdue, due-today, and upcoming pending tasks."""
        if within_days < 0:
            raise ValidationError("within_days must be zero or positive.")
        ref_date = as_of or date.today()
        horizon = ref_date + timedelta(days=within_days)

        overdue: list[Task] = []
        due_today: list[Task] = []
        upcoming: list[Task] = []

        for task in self.storage.list_tasks():
            if task.done or task.due is None:
                continue
            if task.due < ref_date:
                overdue.append(task)
            elif task.due == ref_date:
                due_today.append(task)
            elif task.due <= horizon:
                upcoming.append(task)

        overdue.sort(key=lambda t: (t.due, t.priority.rank, t.id))
        due_today.sort(key=lambda t: (t.priority.rank, t.id))
        upcoming.sort(key=lambda t: (t.due, t.priority.rank, t.id))
        return ReminderReport(
            as_of=ref_date,
            overdue=overdue,
            due_today=due_today,
            upcoming=upcoming,
        )

    def export_csv(self, tasks: Sequence[Task] | None = None) -> str:
        """Render tasks as an RFC-4180 CSV string."""
        items = list(tasks) if tasks is not None else self.list()
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            ["id", "title", "done", "priority", "due", "tags", "recurrence", "created_at", "completed_at"]
        )
        for task in items:
            writer.writerow(
                [
                    task.id,
                    task.title,
                    str(task.done).lower(),
                    task.priority.value,
                    task.due.isoformat() if task.due is not None else "",
                    ",".join(task.tags),
                    task.recurrence.value if task.recurrence is not None else "",
                    task.created_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at is not None else "",
                ]
            )
        return buffer.getvalue()

    def export_markdown(
        self,
        tasks: Sequence[Task] | None = None,
        *,
        style: str = "checklist",
    ) -> str:
        """Render tasks as a Markdown checklist or pipe table."""
        items = list(tasks) if tasks is not None else self.list()
        normalized_style = style.strip().lower()
        if normalized_style not in {"checklist", "table"}:
            raise ValidationError(
                f"Invalid Markdown export style {style!r}. Expected 'checklist' or 'table'."
            )

        if not items:
            return "_No tasks found._\n"

        if normalized_style == "table":
            lines = [
                "| ID | Status | Title | Priority | Due | Tags | Repeat |",
                "|---:|:------:|-------|:--------:|:---:|------|:------:|",
            ]
            for task in items:
                status = "Done" if task.done else ("OVERDUE" if task.is_overdue() else "Pending")
                safe_title = task.title.replace("|", "\\|")
                due_str = task.due.isoformat() if task.due else "-"
                tags_str = ", ".join(f"`{t}`" for t in task.tags) if task.tags else "-"
                repeat_str = task.recurrence.value if task.recurrence else "-"
                lines.append(
                    f"| {task.id} | {status} | {safe_title} | {task.priority.value} | "
                    f"{due_str} | {tags_str} | {repeat_str} |"
                )
            return "\n".join(lines) + "\n"

        lines = []
        for task in items:
            mark = "x" if task.done else " "
            meta: list[str] = [f"priority: {task.priority.value}"]
            if task.due is not None:
                due_note = f"due: {task.due.isoformat()}"
                if task.is_overdue():
                    due_note += " OVERDUE"
                meta.append(due_note)
            if task.tags:
                meta.append("tags: " + ", ".join(f"#{tag}" for tag in task.tags))
            if task.recurrence is not None:
                meta.append(f"repeat: {task.recurrence.value}")
            lines.append(f"- [{mark}] **#{task.id}** {task.title} ({'; '.join(meta)})")
        return "\n".join(lines) + "\n"
