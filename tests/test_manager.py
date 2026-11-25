"""Tests for TaskManager, JsonStorage, SqliteStorage, config, reminders, and exports."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from todo.config import create_storage, resolve_config, save_user_config
from todo.exceptions import (
    CorruptedStorageError,
    NothingToUndoError,
    TaskNotFoundError,
    ValidationError,
)
from todo.manager import TaskManager
from todo.models import Priority, Recurrence
from todo.storage import JsonStorage, SqliteStorage, Storage


@pytest.fixture(params=["json", "sqlite"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Storage:
    """Run every core manager test against both JsonStorage and SqliteStorage."""
    if request.param == "json":
        return JsonStorage(tmp_path / "tasks.json")
    return SqliteStorage(tmp_path / "tasks.db")


@pytest.fixture()
def manager(storage: Storage) -> TaskManager:
    return TaskManager(storage)


def test_add_and_list_defaults(manager: TaskManager) -> None:
    t1 = manager.add("Buy milk")
    t2 = manager.add("Write tests", due="2026-12-01", priority="high", tags=["work, qa", "WORK"])

    assert t1.id == 1
    assert t1.done is False
    assert t1.priority is Priority.MEDIUM
    assert t2.id == 2
    assert t2.due == date(2026, 12, 1)
    assert t2.priority is Priority.HIGH
    assert t2.tags == ["work", "qa"]

    all_tasks = manager.list()
    assert [t.id for t in all_tasks] == [1, 2]


def test_stable_ids_never_reused_after_delete(manager: TaskManager) -> None:
    t1 = manager.add("First")
    t2 = manager.add("Second")
    t3 = manager.add("Third")
    assert [t1.id, t2.id, t3.id] == [1, 2, 3]

    manager.delete(2)
    t4 = manager.add("Fourth")
    assert t4.id == 4
    assert [t.id for t in manager.list()] == [1, 3, 4]

    # Deleting the highest-ID task must still keep next_id advancing.
    manager.delete(4)
    t5 = manager.add("Fifth")
    assert t5.id == 5


def test_complete_reopen_and_delete(manager: TaskManager) -> None:
    task = manager.add("Ship MVP")
    completed = manager.complete(task.id)
    assert completed.done is True
    assert completed.completed_at is not None

    # Completing an already-completed task is idempotent.
    again = manager.complete(task.id)
    assert again.done is True

    reopened = manager.reopen(task.id)
    assert reopened.done is False
    assert reopened.completed_at is None

    deleted = manager.delete(task.id)
    assert deleted.id == task.id
    assert manager.list() == []


def test_missing_task_errors(manager: TaskManager) -> None:
    with pytest.raises(TaskNotFoundError):
        manager.get(999)
    with pytest.raises(TaskNotFoundError):
        manager.complete(999)
    with pytest.raises(TaskNotFoundError):
        manager.reopen(999)
    with pytest.raises(TaskNotFoundError):
        manager.update(999, title="Nope")
    with pytest.raises(TaskNotFoundError):
        manager.delete(999)


def test_validation_errors(manager: TaskManager) -> None:
    with pytest.raises(ValidationError):
        manager.add("")
    with pytest.raises(ValidationError):
        manager.add("   ")
    with pytest.raises(ValidationError):
        manager.add("Bad priority", priority="urgent")
    with pytest.raises(ValidationError):
        manager.add("Bad due", due="2026/12/01")
    with pytest.raises(ValidationError):
        manager.add("Bad repeat", recurrence="hourly")
    with pytest.raises(ValidationError):
        manager.list(sort_by="unknown")
    with pytest.raises(ValidationError):
        manager.reminders(within_days=-1)


def test_filtering_and_sorting(manager: TaskManager) -> None:
    today = date(2026, 11, 26)
    manager.add("Low later", due="2026-12-10", priority="low", tags=["home"])
    t_over = manager.add("Overdue report", due="2026-11-20", priority="high", tags=["work", "finance"])
    t_nodue = manager.add("Unscheduled idea", priority="high", tags=["work"])
    t_done = manager.add("Finished task", due="2026-11-25", priority="medium", tags=["work"])
    manager.complete(t_done.id)

    assert [t.id for t in manager.list(done=False)] == [1, 2, 3]
    assert [t.id for t in manager.list(done=True)] == [4]
    assert [t.id for t in manager.list(tag="WORK")] == [2, 3, 4]
    assert [t.id for t in manager.list(priority="high")] == [2, 3]
    assert [t.id for t in manager.list(search="report")] == [2]
    assert [t.id for t in manager.list(search="finance")] == [2]
    assert [t.id for t in manager.list(overdue_only=True, as_of=today)] == [t_over.id]

    # Sort by due date puts nulls last.
    by_due = manager.list(done=False, sort_by="due")
    assert [t.id for t in by_due] == [2, 1, t_nodue.id]

    # Sort by priority puts high before low, breaking ties with due dates first.
    by_prio = manager.list(done=False, sort_by="priority")
    assert [t.id for t in by_prio] == [2, 3, 1]


def test_update_and_clear_completed(manager: TaskManager) -> None:
    task = manager.add("Draft doc", due="2026-12-01", priority="low", tags=["docs"], recurrence="weekly")
    updated = manager.update(
        task.id,
        title="Publish doc",
        due=None,
        priority="high",
        tags=["docs", "release"],
        recurrence=None,
    )
    assert updated.title == "Publish doc"
    assert updated.due is None
    assert updated.priority is Priority.HIGH
    assert updated.tags == ["docs", "release"]
    assert updated.recurrence is None

    manager.complete(task.id)
    manager.add("Keep pending")
    cleared = manager.clear_completed()
    assert [t.id for t in cleared] == [task.id]
    assert [t.title for t in manager.list()] == ["Keep pending"]


def test_undo_across_mutations(manager: TaskManager) -> None:
    with pytest.raises(NothingToUndoError):
        manager.undo()

    t1 = manager.add("One")
    t2 = manager.add("Two")
    manager.complete(t1.id)
    manager.delete(t2.id)

    assert [t.id for t in manager.list()] == [1]
    assert manager.list()[0].done is True

    manager.undo()  # undo delete #2
    assert [t.id for t in manager.list()] == [1, 2]

    manager.undo()  # undo complete #1
    assert manager.get(1).done is False

    manager.undo()  # undo add #2
    assert [t.id for t in manager.list()] == [1]

    manager.undo()  # undo add #1
    assert manager.list() == []

    with pytest.raises(NothingToUndoError):
        manager.undo()


def test_recurring_tasks_and_month_end_clamping(manager: TaskManager) -> None:
    daily = manager.add("Standup", due="2026-11-26", recurrence="daily")
    monthly = manager.add("Month-end close", due="2026-01-31", recurrence="monthly")

    daily_res = manager.complete_detailed(daily.id)
    assert daily_res.completed_task.done is True
    assert daily_res.next_task is not None
    assert daily_res.next_task.due == date(2026, 11, 27)
    assert daily_res.next_task.recurrence is Recurrence.DAILY

    monthly_res = manager.complete_detailed(monthly.id)
    assert monthly_res.next_task is not None
    assert monthly_res.next_task.due == date(2026, 2, 28)

    # Undoing a recurring completion reverts both completion and spawned follow-up.
    manager.undo()
    assert manager.get(monthly.id).done is False
    assert len(manager.list()) == 3  # daily (done), daily next, monthly (pending)


def test_reminders_categorization(manager: TaskManager) -> None:
    ref = date(2026, 11, 26)
    overdue = manager.add("Late invoice", due=ref - timedelta(days=2), priority="high")
    today_task = manager.add("Submit PR", due=ref, priority="medium")
    soon = manager.add("Demo prep", due=ref + timedelta(days=2), priority="low")
    later = manager.add("Far future", due=ref + timedelta(days=10))
    done_late = manager.add("Already done", due=ref - timedelta(days=5))
    manager.complete(done_late.id)

    report = manager.reminders(within_days=3, as_of=ref)
    assert [t.id for t in report.overdue] == [overdue.id]
    assert [t.id for t in report.due_today] == [today_task.id]
    assert [t.id for t in report.upcoming] == [soon.id]
    assert later.id not in [t.id for t in report.upcoming]
    assert report.total_count == 3


def test_export_csv_and_markdown(manager: TaskManager) -> None:
    t1 = manager.add("Buy milk, eggs", due="2026-12-01", priority="high", tags=["home", "food"])
    t2 = manager.add("Fix | pipe", priority="low", recurrence="weekly")
    manager.complete(t1.id)

    csv_out = manager.export_csv()
    rows = list(csv.DictReader(io.StringIO(csv_out)))
    assert len(rows) == 2
    assert rows[0]["title"] == "Buy milk, eggs"
    assert rows[0]["done"] == "true"
    assert rows[1]["recurrence"] == "weekly"

    md_checklist = manager.export_markdown(style="checklist")
    assert "- [x] **#1** Buy milk, eggs" in md_checklist
    assert "- [ ] **#2** Fix | pipe" in md_checklist

    md_table = manager.export_markdown(style="table")
    assert "| ID | Status | Title |" in md_table
    assert "Fix \\| pipe" in md_table


def test_json_storage_missing_and_legacy_file(tmp_path: Path) -> None:
    json_path = tmp_path / "tasks.json"
    storage = JsonStorage(json_path)
    assert storage.list_tasks() == []

    # Legacy starter-script format (plain array without IDs).
    json_path.write_text(json.dumps([{"title": "Old task", "done": False}]), encoding="utf-8")
    loaded = storage.list_tasks()
    assert len(loaded) == 1
    assert loaded[0].id == 1
    assert loaded[0].title == "Old task"


@pytest.mark.parametrize(
    "bad_content",
    [
        "",
        "{not valid json",
        "123",
        json.dumps({"next_id": "one", "tasks": []}),
        json.dumps({"next_id": 1, "tasks": "not-a-list"}),
        json.dumps([{"id": 1, "title": "", "done": False}]),
        json.dumps([{"id": 1, "title": "Bad done", "done": "yes"}]),
        json.dumps([{"id": 1, "title": "A"}, {"id": 1, "title": "Duplicate"}]),
    ],
)
def test_json_storage_corrupted_files(tmp_path: Path, bad_content: str) -> None:
    json_path = tmp_path / "tasks.json"
    json_path.write_text(bad_content, encoding="utf-8")
    storage = JsonStorage(json_path)
    with pytest.raises(CorruptedStorageError):
        storage.list_tasks()


def test_json_storage_atomic_write_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_path = tmp_path / "tasks.json"
    storage = JsonStorage(json_path)
    mgr = TaskManager(storage)
    mgr.add("Original safe task")
    original_bytes = json_path.read_bytes()

    def failing_replace(src: Path, dst: Path) -> None:
        raise OSError("Simulated disk failure during replace")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(Exception):
        mgr.add("Should fail atomically")

    assert json_path.read_bytes() == original_bytes
    leftover_tmps = list(tmp_path.glob("*.tmp"))
    assert leftover_tmps == []


def test_sqlite_storage_corrupted_and_memory(tmp_path: Path) -> None:
    mem_mgr = TaskManager(SqliteStorage(":memory:"))
    assert mem_mgr.add("In memory").id == 1

    bad_db = tmp_path / "corrupt.db"
    bad_db.write_bytes(b"definitely not a sqlite database")
    with pytest.raises(CorruptedStorageError):
        SqliteStorage(bad_db)


def test_config_resolution_and_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = tmp_path / "config.toml"
    monkeypatch.delenv("TODO_STORAGE", raising=False)
    monkeypatch.delenv("TODO_PATH", raising=False)
    monkeypatch.delenv("TODO_DB", raising=False)

    saved = save_user_config(backend="sqlite", path=tmp_path / "persisted.db", config_path=cfg_file)
    assert saved == cfg_file

    cfg = resolve_config(config_path=cfg_file)
    assert cfg.backend == "sqlite"
    assert cfg.path == tmp_path / "persisted.db"

    # Env overrides config file; explicit args override env.
    monkeypatch.setenv("TODO_STORAGE", "json")
    monkeypatch.setenv("TODO_PATH", str(tmp_path / "env.json"))
    env_cfg = resolve_config(config_path=cfg_file)
    assert env_cfg.backend == "json"
    assert env_cfg.path == tmp_path / "env.json"

    flag_storage = create_storage(backend="sqlite", path=tmp_path / "flag.db", config_path=cfg_file)
    assert isinstance(flag_storage, SqliteStorage)
    assert flag_storage.path == tmp_path / "flag.db"
