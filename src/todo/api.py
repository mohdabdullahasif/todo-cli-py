"""FastAPI REST wrapper over TaskManager (stretch goal)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from todo.config import create_storage
from todo.exceptions import (
    NothingToUndoError,
    StorageError,
    TaskNotFoundError,
    ValidationError,
)
from todo.manager import TaskManager
from todo.models import Priority, Recurrence, Task


class TaskCreateRequest(BaseModel):
    """Payload to create a new task."""

    title: str = Field(..., min_length=1, description="Task title")
    due: date | None = Field(default=None, description="Optional due date (YYYY-MM-DD)")
    priority: Priority = Field(default=Priority.MEDIUM, description="Task priority")
    tags: list[str] = Field(default_factory=list, description="List of tags")
    recurrence: Recurrence | None = Field(default=None, description="Optional recurrence")


class TaskUpdateRequest(BaseModel):
    """Payload to partially update an existing task."""

    title: str | None = None
    due: date | None = None
    clear_due: bool = False
    priority: Priority | None = None
    tags: list[str] | None = None
    recurrence: Recurrence | None = None
    clear_recurrence: bool = False
    done: bool | None = None


class TaskResponse(BaseModel):
    """Serialized task returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    done: bool
    due: date | None
    priority: Priority
    tags: list[str]
    created_at: datetime
    completed_at: datetime | None
    recurrence: Recurrence | None
    overdue: bool

    @classmethod
    def from_task(cls, task: Task) -> TaskResponse:
        return cls(
            id=task.id,
            title=task.title,
            done=task.done,
            due=task.due,
            priority=task.priority,
            tags=list(task.tags),
            created_at=task.created_at,
            completed_at=task.completed_at,
            recurrence=task.recurrence,
            overdue=task.is_overdue(),
        )


class CompletionResponse(BaseModel):
    """Response for completing a task."""

    completed_task: TaskResponse
    next_task: TaskResponse | None = None


class ReminderResponse(BaseModel):
    """Categorized reminder payload."""

    as_of: date
    overdue: list[TaskResponse]
    due_today: list[TaskResponse]
    upcoming: list[TaskResponse]
    total_count: int


class UndoResponse(BaseModel):
    """Response after undoing the last mutation."""

    undone: str


def create_app(manager: TaskManager | None = None) -> FastAPI:
    """Create a FastAPI application backed by TaskManager."""
    api = FastAPI(
        title="Advanced CLI To-Do Manager API",
        version="0.1.0",
        description="REST API wrapper over TaskManager supporting JSON and SQLite backends.",
    )

    def get_manager() -> TaskManager:
        if manager is not None:
            return manager
        return TaskManager(create_storage())

    @api.exception_handler(TaskNotFoundError)
    async def _not_found_handler(_: Request, exc: TaskNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @api.exception_handler(ValidationError)
    async def _validation_handler(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @api.exception_handler(NothingToUndoError)
    async def _undo_handler(_: Request, exc: NothingToUndoError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @api.exception_handler(StorageError)
    async def _storage_handler(_: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/tasks", response_model=list[TaskResponse])
    def list_tasks(
        status: Literal["all", "pending", "done"] = "all",
        tag: str | None = None,
        priority: Priority | None = None,
        search: str | None = None,
        sort_by: Literal["id", "due", "priority", "created"] = "id",
        descending: bool = False,
        overdue_only: bool = False,
        tm: TaskManager = Depends(get_manager),
    ) -> list[TaskResponse]:
        done_filter = None if status == "all" else (status == "done")
        tasks = tm.list(
            done=done_filter,
            tag=tag,
            priority=priority,
            search=search,
            sort_by=sort_by,
            descending=descending,
            overdue_only=overdue_only,
        )
        return [TaskResponse.from_task(task) for task in tasks]

    @api.post("/tasks", response_model=TaskResponse, status_code=201)
    def create_task(
        payload: TaskCreateRequest,
        tm: TaskManager = Depends(get_manager),
    ) -> TaskResponse:
        created = tm.add(
            payload.title,
            due=payload.due,
            priority=payload.priority,
            tags=payload.tags,
            recurrence=payload.recurrence,
        )
        return TaskResponse.from_task(created)

    @api.get("/tasks/{task_id}", response_model=TaskResponse)
    def get_task(task_id: int, tm: TaskManager = Depends(get_manager)) -> TaskResponse:
        return TaskResponse.from_task(tm.get(task_id))

    @api.patch("/tasks/{task_id}", response_model=TaskResponse)
    def update_task(
        task_id: int,
        payload: TaskUpdateRequest,
        tm: TaskManager = Depends(get_manager),
    ) -> TaskResponse:
        kwargs: dict[str, object] = {}
        if payload.title is not None:
            kwargs["title"] = payload.title
        if payload.clear_due:
            kwargs["due"] = None
        elif payload.due is not None:
            kwargs["due"] = payload.due
        if payload.priority is not None:
            kwargs["priority"] = payload.priority
        if payload.tags is not None:
            kwargs["tags"] = payload.tags
        if payload.clear_recurrence:
            kwargs["recurrence"] = None
        elif payload.recurrence is not None:
            kwargs["recurrence"] = payload.recurrence
        if payload.done is not None:
            kwargs["done"] = payload.done
        updated = tm.update(task_id, **kwargs)  # type: ignore[arg-type]
        return TaskResponse.from_task(updated)

    @api.post("/tasks/{task_id}/complete", response_model=CompletionResponse)
    def complete_task(
        task_id: int,
        tm: TaskManager = Depends(get_manager),
    ) -> CompletionResponse:
        result = tm.complete_detailed(task_id)
        return CompletionResponse(
            completed_task=TaskResponse.from_task(result.completed_task),
            next_task=TaskResponse.from_task(result.next_task) if result.next_task else None,
        )

    @api.delete("/tasks/{task_id}", response_model=TaskResponse)
    def delete_task(
        task_id: int,
        tm: TaskManager = Depends(get_manager),
    ) -> TaskResponse:
        return TaskResponse.from_task(tm.delete(task_id))

    @api.get("/reminders", response_model=ReminderResponse)
    def get_reminders(
        within_days: int = Query(default=3, ge=0),
        tm: TaskManager = Depends(get_manager),
    ) -> ReminderResponse:
        report = tm.reminders(within_days=within_days)
        return ReminderResponse(
            as_of=report.as_of,
            overdue=[TaskResponse.from_task(t) for t in report.overdue],
            due_today=[TaskResponse.from_task(t) for t in report.due_today],
            upcoming=[TaskResponse.from_task(t) for t in report.upcoming],
            total_count=report.total_count,
        )

    @api.post("/undo", response_model=UndoResponse)
    def undo_action(tm: TaskManager = Depends(get_manager)) -> UndoResponse:
        return UndoResponse(undone=tm.undo())

    @api.get("/export")
    def export_tasks(
        format: Literal["csv", "markdown"] = "markdown",
        style: Literal["checklist", "table"] = "checklist",
        tm: TaskManager = Depends(get_manager),
    ) -> PlainTextResponse:
        tasks = tm.list()
        if format == "csv":
            return PlainTextResponse(
                content=tm.export_csv(tasks),
                media_type="text/csv",
            )
        return PlainTextResponse(
            content=tm.export_markdown(tasks, style=style),
            media_type="text/markdown",
        )

    return api


app = create_app()
