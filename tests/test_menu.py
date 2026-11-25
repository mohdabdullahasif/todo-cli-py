"""Tests for the refactored interactive menu app running on TaskManager (MVP 1)."""

from __future__ import annotations

from pathlib import Path

from todo.manager import TaskManager
from todo.menu import run_menu
from todo.storage import JsonStorage


def test_interactive_menu_flow(tmp_path: Path) -> None:
    manager = TaskManager(JsonStorage(tmp_path / "menu_tasks.json"))
    inputs = iter(
        [
            "9",          # invalid menu option
            "1",          # view empty
            "2", "Buy tea",
            "2", "Read book",
            "1",          # view 2 tasks
            "3", "bad",   # invalid number
            "3", "1",     # complete task 1
            "4", "2",     # delete task 2
            "1",          # view remaining
            "5",          # quit
        ]
    )
    outputs: list[str] = []

    run_menu(manager, input_fn=lambda _: next(inputs), output_fn=outputs.append)

    joined = "\n".join(outputs)
    assert "Pick 1-5." in joined
    assert "No tasks yet." in joined
    assert "Added #1: Buy tea." in joined
    assert "Invalid number." in joined
    assert "Marked #1 done." in joined
    assert "Deleted 'Read book'." in joined
    assert "1. [x] Buy tea" in joined
    assert "Bye!" in joined

    persisted = manager.list()
    assert len(persisted) == 1
    assert persisted[0].title == "Buy tea"
    assert persisted[0].done is True
