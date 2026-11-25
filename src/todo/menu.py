"""Interactive menu application running on top of TaskManager (MVP 1)."""

from __future__ import annotations

from collections.abc import Callable

from todo.config import create_storage
from todo.exceptions import TodoError
from todo.manager import TaskManager
from todo.models import Task

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def show_tasks(tasks: list[Task], output_fn: OutputFn = print) -> None:
    """Display tasks in the numbered menu format."""
    if not tasks:
        output_fn("\nNo tasks yet.")
        return
    output_fn("")
    for index, task in enumerate(tasks, start=1):
        mark = "x" if task.done else " "
        details: list[str] = [f"#{task.id}", task.priority.value]
        if task.due is not None:
            due_str = f"due {task.due.isoformat()}"
            if task.is_overdue():
                due_str += " OVERDUE"
            details.append(due_str)
        if task.tags:
            details.append("tags: " + ", ".join(task.tags))
        output_fn(f"{index}. [{mark}] {task.title} ({'; '.join(details)})")


def _select_task(
    tasks: list[Task],
    prompt: str,
    *,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> Task | None:
    if not tasks:
        output_fn("No tasks available.")
        return None
    raw = input_fn(prompt).strip()
    try:
        index = int(raw) - 1
    except ValueError:
        output_fn("Invalid number.")
        return None
    if 0 <= index < len(tasks):
        return tasks[index]
    output_fn("Invalid number.")
    return None


def add_task(
    manager: TaskManager,
    *,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> None:
    """Prompt for a task title and add it via TaskManager."""
    title = input_fn("Task: ").strip()
    if not title:
        return
    try:
        created = manager.add(title)
    except TodoError as exc:
        output_fn(f"Error: {exc}")
        return
    output_fn(f"Added #{created.id}: {created.title}.")


def complete_task(
    manager: TaskManager,
    *,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> None:
    """Prompt for a displayed task number and mark it done via TaskManager."""
    tasks = manager.list()
    selected = _select_task(
        tasks,
        "Task number to complete: ",
        input_fn=input_fn,
        output_fn=output_fn,
    )
    if selected is None:
        return
    try:
        result = manager.complete_detailed(selected.id)
    except TodoError as exc:
        output_fn(f"Error: {exc}")
        return
    output_fn(f"Marked #{result.completed_task.id} done.")
    if result.next_task is not None:
        due_str = result.next_task.due.isoformat() if result.next_task.due else "unscheduled"
        output_fn(
            f"Scheduled next occurrence #{result.next_task.id} (due {due_str})."
        )


def delete_task(
    manager: TaskManager,
    *,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> None:
    """Prompt for a displayed task number and delete it via TaskManager."""
    tasks = manager.list()
    selected = _select_task(
        tasks,
        "Task number to delete: ",
        input_fn=input_fn,
        output_fn=output_fn,
    )
    if selected is None:
        return
    try:
        removed = manager.delete(selected.id)
    except TodoError as exc:
        output_fn(f"Error: {exc}")
        return
    output_fn(f"Deleted '{removed.title}'.")


def run_menu(
    manager: TaskManager | None = None,
    *,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> None:
    """Run the interactive text menu backed by TaskManager."""
    active_manager = manager or TaskManager(create_storage())
    while True:
        output_fn("\n1) View  2) Add  3) Complete  4) Delete  5) Quit")
        try:
            choice = input_fn("> ").strip()
        except EOFError:
            output_fn("Bye!")
            break
        if choice == "5":
            output_fn("Bye!")
            break
        if choice == "1":
            try:
                show_tasks(active_manager.list(), output_fn=output_fn)
            except TodoError as exc:
                output_fn(f"Error: {exc}")
        elif choice == "2":
            add_task(active_manager, input_fn=input_fn, output_fn=output_fn)
        elif choice == "3":
            complete_task(active_manager, input_fn=input_fn, output_fn=output_fn)
        elif choice == "4":
            delete_task(active_manager, input_fn=input_fn, output_fn=output_fn)
        else:
            output_fn("Pick 1-5.")


def main() -> None:
    """Entrypoint for running the interactive menu directly."""
    run_menu()


if __name__ == "__main__":
    main()
