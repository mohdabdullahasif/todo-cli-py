"""Custom exceptions for the Todo task manager."""

from __future__ import annotations


class TodoError(Exception):
    """Base exception for all Todo application errors."""


class TaskNotFoundError(TodoError):
    """Raised when a requested task ID does not exist."""

    def __init__(self, task_id: int) -> None:
        self.task_id = task_id
        super().__init__(f"Task #{task_id} was not found.")


class ValidationError(TodoError):
    """Raised when user input or task metadata fails validation."""


class StorageError(TodoError):
    """Raised when reading from or writing to storage fails."""


class CorruptedStorageError(StorageError):
    """Raised when a storage file or database contains invalid or corrupted data."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Storage '{path}' is corrupted: {reason}")


class NothingToUndoError(TodoError):
    """Raised when attempting to undo with no recorded history."""

    def __init__(self) -> None:
        super().__init__("Nothing to undo.")
