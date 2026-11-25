"""Tests for the FastAPI wrapper over TaskManager."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from todo.api import create_app
from todo.manager import TaskManager
from todo.storage import SqliteStorage


def test_fastapi_endpoints(tmp_path: Path) -> None:
    manager = TaskManager(SqliteStorage(tmp_path / "api.db"))
    client = TestClient(create_app(manager))

    assert client.get("/health").json() == {"status": "ok"}

    # Create a recurring high-priority task
    created = client.post(
        "/tasks",
        json={
            "title": "Pay rent",
            "due": "2026-12-01",
            "priority": "high",
            "tags": ["finance"],
            "recurrence": "monthly",
        },
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert task_id == 1

    # List & filter
    listed = client.get("/tasks", params={"tag": "finance", "sort_by": "due"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # Update metadata
    patched = client.patch(
        f"/tasks/{task_id}",
        json={"title": "Pay December rent", "priority": "medium"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Pay December rent"
    assert patched.json()["priority"] == "medium"

    # Complete spawns next monthly occurrence
    completed = client.post(f"/tasks/{task_id}/complete")
    assert completed.status_code == 200
    body = completed.json()
    assert body["completed_task"]["done"] is True
    assert body["next_task"]["due"] == "2027-01-01"

    # Reminders & Export
    rem = client.get("/reminders", params={"within_days": 3650})
    assert rem.status_code == 200
    assert rem.json()["total_count"] >= 1

    csv_exp = client.get("/export", params={"format": "csv"})
    assert csv_exp.status_code == 200
    assert "Pay December rent" in csv_exp.text

    md_exp = client.get("/export", params={"format": "markdown", "style": "table"})
    assert md_exp.status_code == 200
    assert "| ID | Status | Title |" in md_exp.text

    # Delete & Undo & Not Found
    deleted = client.delete(f"/tasks/{task_id}")
    assert deleted.status_code == 200

    missing = client.get(f"/tasks/{task_id}")
    assert missing.status_code == 404

    undone = client.post("/undo")
    assert undone.status_code == 200
    assert client.get(f"/tasks/{task_id}").status_code == 200

    # Clear all undo history -> 409 when empty
    while client.post("/undo").status_code == 200:
        pass
    assert client.post("/undo").status_code == 409
