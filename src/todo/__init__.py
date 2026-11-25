"""Advanced CLI To-Do Manager package."""

from __future__ import annotations

from todo.config import TodoConfig, create_storage, resolve_config, save_user_config
from todo.exceptions import (
    CorruptedStorageError,
    NothingToUndoError,
    StorageError,
    TaskNotFoundError,
    TodoError,
    ValidationError,
)
from todo.manager import TaskManager
from todo.models import CompletionResult, Priority, Recurrence, ReminderReport, Task
from todo.storage import HistoryEntry, JsonStorage, SqliteStorage, Storage

__all__ = [
    "CompletionResult",
    "CorruptedStorageError",
    "HistoryEntry",
    "JsonStorage",
    "NothingToUndoError",
    "Priority",
    "Recurrence",
    "ReminderReport",
    "SqliteStorage",
    "Storage",
    "StorageError",
    "Task",
    "TaskManager",
    "TaskNotFoundError",
    "TodoConfig",
    "TodoError",
    "ValidationError",
    "create_storage",
    "resolve_config",
    "save_user_config",
]
