"""Storage abstraction and JSON/SQLite implementations for Todo tasks."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from todo.exceptions import CorruptedStorageError, StorageError, TaskNotFoundError, ValidationError
from todo.models import Priority, Recurrence, Task, normalize_tags, parse_datetime, parse_due_date


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """Snapshot stored on the undo stack before a mutating operation."""

    description: str
    next_id: int
    tasks: list[Task]


class Storage(ABC):
    """Abstract persistence interface for tasks, ID allocation, and undo history."""

    @abstractmethod
    def list_tasks(self) -> list[Task]:
        """Return all persisted tasks."""

    @abstractmethod
    def get_task(self, task_id: int) -> Task | None:
        """Fetch a single task by ID, or None if absent."""

    @abstractmethod
    def add_task(self, task: Task) -> Task:
        """Persist a new task, allocating a stable ID when task.id <= 0."""

    @abstractmethod
    def update_task(self, task: Task) -> Task:
        """Persist changes to an existing task or raise TaskNotFoundError."""

    @abstractmethod
    def delete_task(self, task_id: int) -> Task:
        """Delete and return an existing task or raise TaskNotFoundError."""

    @abstractmethod
    def export_state(self) -> tuple[list[Task], int]:
        """Return a snapshot tuple of (tasks, next_id)."""

    @abstractmethod
    def replace_state(self, tasks: Sequence[Task], next_id: int) -> None:
        """Replace all stored tasks and the next_id counter atomically."""

    @abstractmethod
    def push_history(self, entry: HistoryEntry, max_entries: int = 20) -> None:
        """Append an undo snapshot, keeping at most max_entries items."""

    @abstractmethod
    def pop_history(self) -> HistoryEntry | None:
        """Pop and return the most recent undo snapshot, or None if empty."""


def _serialize_tasks(tasks: Sequence[Task]) -> list[dict[str, Any]]:
    return [task.to_dict() for task in tasks]


def _deserialize_tasks(raw_tasks: Any, source_path: str) -> list[Task]:
    if not isinstance(raw_tasks, list):
        raise CorruptedStorageError(source_path, "'tasks' must be a JSON array.")
    tasks: list[Task] = []
    seen_ids: set[int] = set()
    for idx, item in enumerate(raw_tasks, start=1):
        try:
            # Support legacy starter-script entries without an explicit 'id' field.
            fallback_id = idx if isinstance(item, dict) and "id" not in item else None
            task = Task.from_dict(item, fallback_id=fallback_id)
        except ValidationError as exc:
            raise CorruptedStorageError(source_path, f"invalid task at index {idx - 1}: {exc}") from exc
        if task.id in seen_ids:
            raise CorruptedStorageError(source_path, f"duplicate task id {task.id}.")
        seen_ids.add(task.id)
        tasks.append(task)
    return tasks


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically to path via a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise StorageError(f"Failed to write '{path}': {exc}") from exc


class JsonStorage(Storage):
    """JSON file-backed Storage implementation using atomic file replacement."""

    def __init__(self, path: str | Path = "tasks.json") -> None:
        self.path = Path(path)
        self.history_path = self.path.with_name(f".{self.path.stem}.history.json")
        self._lock = threading.RLock()

    def _read_json_file(self, path: Path) -> Any:
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"Failed to read '{path}': {exc}") from exc
        if not raw_text.strip():
            raise CorruptedStorageError(str(path), "file is empty.")
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise CorruptedStorageError(str(path), f"invalid JSON ({exc.msg}).") from exc

    def _load_state(self) -> tuple[list[Task], int]:
        if not self.path.exists():
            return [], 1
        data = self._read_json_file(self.path)
        if isinstance(data, list):
            tasks = _deserialize_tasks(data, str(self.path))
            next_id = max((task.id for task in tasks), default=0) + 1
            return tasks, next_id
        if isinstance(data, dict):
            tasks = _deserialize_tasks(data.get("tasks"), str(self.path))
            max_existing = max((task.id for task in tasks), default=0)
            raw_next_id = data.get("next_id", max_existing + 1)
            if isinstance(raw_next_id, bool) or not isinstance(raw_next_id, int) or raw_next_id < 1:
                raise CorruptedStorageError(
                    str(self.path), f"'next_id' must be a positive integer, got {raw_next_id!r}."
                )
            return tasks, max(raw_next_id, max_existing + 1)
        raise CorruptedStorageError(str(self.path), "root JSON value must be an object or array.")

    def _save_state(self, tasks: Sequence[Task], next_id: int) -> None:
        payload = {
            "next_id": max(next_id, max((t.id for t in tasks), default=0) + 1),
            "tasks": _serialize_tasks(tasks),
        }
        _atomic_write_text(self.path, json.dumps(payload, indent=2) + "\n")

    def list_tasks(self) -> list[Task]:
        with self._lock:
            tasks, _ = self._load_state()
            return [task.copy() for task in tasks]

    def get_task(self, task_id: int) -> Task | None:
        with self._lock:
            tasks, _ = self._load_state()
            for task in tasks:
                if task.id == task_id:
                    return task.copy()
            return None

    def add_task(self, task: Task) -> Task:
        with self._lock:
            tasks, next_id = self._load_state()
            assigned_id = task.id if task.id > 0 else next_id
            if any(existing.id == assigned_id for existing in tasks):
                raise StorageError(f"Task ID {assigned_id} already exists.")
            saved = task.copy()
            saved.id = assigned_id
            tasks.append(saved)
            new_next_id = max(next_id, assigned_id + 1)
            self._save_state(tasks, new_next_id)
            return saved.copy()

    def update_task(self, task: Task) -> Task:
        with self._lock:
            tasks, next_id = self._load_state()
            for idx, existing in enumerate(tasks):
                if existing.id == task.id:
                    updated = task.copy()
                    tasks[idx] = updated
                    self._save_state(tasks, next_id)
                    return updated.copy()
            raise TaskNotFoundError(task.id)

    def delete_task(self, task_id: int) -> Task:
        with self._lock:
            tasks, next_id = self._load_state()
            for idx, existing in enumerate(tasks):
                if existing.id == task_id:
                    removed = tasks.pop(idx)
                    self._save_state(tasks, next_id)
                    return removed.copy()
            raise TaskNotFoundError(task_id)

    def export_state(self) -> tuple[list[Task], int]:
        with self._lock:
            tasks, next_id = self._load_state()
            return [task.copy() for task in tasks], next_id

    def replace_state(self, tasks: Sequence[Task], next_id: int) -> None:
        with self._lock:
            copies = [task.copy() for task in tasks]
            self._save_state(copies, next_id)

    def _load_history_raw(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        try:
            data = self._read_json_file(self.history_path)
        except CorruptedStorageError:
            return []
        if not isinstance(data, list):
            return []
        return [entry for entry in data if isinstance(entry, dict)]

    def push_history(self, entry: HistoryEntry, max_entries: int = 20) -> None:
        with self._lock:
            stack = self._load_history_raw()
            stack.append(
                {
                    "description": entry.description,
                    "next_id": entry.next_id,
                    "tasks": _serialize_tasks(entry.tasks),
                }
            )
            if max_entries > 0 and len(stack) > max_entries:
                stack = stack[-max_entries:]
            _atomic_write_text(self.history_path, json.dumps(stack, indent=2) + "\n")

    def pop_history(self) -> HistoryEntry | None:
        with self._lock:
            stack = self._load_history_raw()
            if not stack:
                return None
            raw = stack.pop()
            if stack:
                _atomic_write_text(self.history_path, json.dumps(stack, indent=2) + "\n")
            else:
                self.history_path.unlink(missing_ok=True)
            tasks = _deserialize_tasks(raw.get("tasks", []), str(self.history_path))
            next_id = raw.get("next_id", max((t.id for t in tasks), default=0) + 1)
            if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id < 1:
                next_id = max((t.id for t in tasks), default=0) + 1
            return HistoryEntry(
                description=str(raw.get("description", "last action")),
                next_id=next_id,
                tasks=tasks,
            )


class SqliteStorage(Storage):
    """SQLite-backed Storage implementation with transactional operations."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        done INTEGER NOT NULL,
        due TEXT,
        priority TEXT NOT NULL,
        tags TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        recurrence TEXT
    );
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS history (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT NOT NULL,
        next_id INTEGER NOT NULL,
        tasks_json TEXT NOT NULL
    );
    """

    def __init__(self, path: str | Path = "tasks.db") -> None:
        self._is_memory = str(path) == ":memory:"
        self.path = Path(path) if not self._is_memory else Path(":memory:")
        self._lock = threading.RLock()
        self._memory_conn: sqlite3.Connection | None = None
        if self._is_memory:
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._is_memory:
            assert self._memory_conn is not None
            try:
                yield self._memory_conn
                self._memory_conn.commit()
            except sqlite3.DatabaseError as exc:
                self._memory_conn.rollback()
                raise CorruptedStorageError(":memory:", str(exc)) from exc
            except Exception:
                self._memory_conn.rollback()
                raise
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Failed to open SQLite database '{self.path}': {exc}") from exc
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            raise CorruptedStorageError(str(self.path), str(exc)) from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            if not self._is_memory:
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(self._SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('next_id', '1')"
            )

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        try:
            raw_tags = json.loads(row["tags"]) if row["tags"] else []
            if not isinstance(raw_tags, list):
                raise ValidationError("Stored tags column must be a JSON array.")
            return Task(
                id=int(row["id"]),
                title=row["title"],
                done=bool(row["done"]),
                due=parse_due_date(row["due"]),
                priority=Priority.parse(row["priority"]),
                tags=normalize_tags(raw_tags),
                created_at=parse_datetime(row["created_at"]),
                completed_at=parse_datetime(row["completed_at"]) if row["completed_at"] else None,
                recurrence=Recurrence.parse(row["recurrence"]),
            )
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise CorruptedStorageError(str(self.path), f"invalid task row: {exc}") from exc

    def _task_params(self, task: Task) -> tuple[Any, ...]:
        return (
            task.id,
            task.title,
            int(task.done),
            task.due.isoformat() if task.due is not None else None,
            task.priority.value,
            json.dumps(list(task.tags)),
            task.created_at.isoformat(),
            task.completed_at.isoformat() if task.completed_at is not None else None,
            task.recurrence.value if task.recurrence is not None else None,
        )

    def _get_next_id(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'next_id'").fetchone()
        max_row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM tasks").fetchone()
        max_id = int(max_row["max_id"]) if max_row else 0
        if row is None:
            return max_id + 1
        try:
            stored_next = int(row["value"])
        except (TypeError, ValueError) as exc:
            raise CorruptedStorageError(str(self.path), "invalid 'next_id' metadata.") from exc
        return max(stored_next, max_id + 1)

    def _set_next_id(self, conn: sqlite3.Connection, next_id: int) -> None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('next_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(max(next_id, 1)),),
        )

    def list_tasks(self) -> list[Task]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id ASC").fetchall()
            return [self._row_to_task(row) for row in rows]

    def get_task(self, task_id: int) -> Task | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row is not None else None

    def add_task(self, task: Task) -> Task:
        with self._lock, self._connect() as conn:
            next_id = self._get_next_id(conn)
            assigned_id = task.id if task.id > 0 else next_id
            saved = task.copy()
            saved.id = assigned_id
            try:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, title, done, due, priority, tags, created_at, completed_at, recurrence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._task_params(saved),
                )
            except sqlite3.IntegrityError as exc:
                raise StorageError(f"Task ID {assigned_id} already exists.") from exc
            self._set_next_id(conn, max(next_id, assigned_id + 1))
            return saved.copy()

    def update_task(self, task: Task) -> Task:
        with self._lock, self._connect() as conn:
            updated = task.copy()
            cursor = conn.execute(
                """
                UPDATE tasks
                SET title = ?, done = ?, due = ?, priority = ?, tags = ?,
                    created_at = ?, completed_at = ?, recurrence = ?
                WHERE id = ?
                """,
                (
                    updated.title,
                    int(updated.done),
                    updated.due.isoformat() if updated.due is not None else None,
                    updated.priority.value,
                    json.dumps(list(updated.tags)),
                    updated.created_at.isoformat(),
                    updated.completed_at.isoformat() if updated.completed_at is not None else None,
                    updated.recurrence.value if updated.recurrence is not None else None,
                    updated.id,
                ),
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(task.id)
            return updated.copy()

    def delete_task(self, task_id: int) -> Task:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise TaskNotFoundError(task_id)
            removed = self._row_to_task(row)
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return removed

    def export_state(self) -> tuple[list[Task], int]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id ASC").fetchall()
            tasks = [self._row_to_task(row) for row in rows]
            next_id = self._get_next_id(conn)
            return tasks, next_id

    def replace_state(self, tasks: Sequence[Task], next_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM tasks")
            for task in tasks:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, title, done, due, priority, tags, created_at, completed_at, recurrence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._task_params(task),
                )
            computed_next = max(next_id, max((t.id for t in tasks), default=0) + 1)
            self._set_next_id(conn, computed_next)

    def push_history(self, entry: HistoryEntry, max_entries: int = 20) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO history (description, next_id, tasks_json) VALUES (?, ?, ?)",
                (entry.description, entry.next_id, json.dumps(_serialize_tasks(entry.tasks))),
            )
            if max_entries > 0:
                conn.execute(
                    """
                    DELETE FROM history
                    WHERE seq NOT IN (
                        SELECT seq FROM history ORDER BY seq DESC LIMIT ?
                    )
                    """,
                    (max_entries,),
                )

    def pop_history(self) -> HistoryEntry | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT seq, description, next_id, tasks_json FROM history ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM history WHERE seq = ?", (row["seq"],))
            try:
                raw_tasks = json.loads(row["tasks_json"])
            except json.JSONDecodeError as exc:
                raise CorruptedStorageError(str(self.path), f"invalid history entry: {exc}") from exc
            tasks = _deserialize_tasks(raw_tasks, str(self.path))
            return HistoryEntry(
                description=str(row["description"]),
                next_id=int(row["next_id"]),
                tasks=tasks,
            )
