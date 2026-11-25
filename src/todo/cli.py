"""Typer CLI with Rich output for the Advanced CLI To-Do Manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from todo.config import DEFAULT_CONFIG_PATH, create_storage, resolve_config, save_user_config
from todo.exceptions import TodoError, ValidationError
from todo.logging_config import configure_logging
from todo.manager import TaskManager
from todo.menu import run_menu
from todo.models import Priority, Task
from todo.storage import _atomic_write_text

app = typer.Typer(
    name="todo",
    help="Advanced CLI To-Do Manager with JSON/SQLite storage, reminders, and export.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


@dataclass(slots=True)
class CliContext:
    """Shared CLI options passed via Typer context."""

    backend: str | None
    db_path: Path | None
    verbose: bool


def _get_manager(ctx: typer.Context) -> TaskManager:
    state = ctx.obj
    if isinstance(state, CliContext):
        storage = create_storage(backend=state.backend, path=state.db_path)
    else:
        storage = create_storage()
    return TaskManager(storage)


def _priority_text(priority: Priority) -> Text:
    styles = {
        Priority.HIGH: "bold red",
        Priority.MEDIUM: "yellow",
        Priority.LOW: "green",
    }
    return Text(priority.value, style=styles[priority])


def _status_text(task: Task, *, today: date) -> Text:
    if task.done:
        return Text("Done", style="green")
    if task.is_overdue(as_of=today):
        return Text("OVERDUE", style="bold red")
    if task.is_due_today(as_of=today):
        return Text("DUE TODAY", style="bold yellow")
    return Text("Pending", style="cyan")


def _due_text(task: Task, *, today: date) -> Text:
    if task.due is None:
        return Text("-", style="dim")
    due_str = task.due.isoformat()
    if task.is_overdue(as_of=today):
        return Text(due_str, style="bold red")
    if task.is_due_today(as_of=today):
        return Text(due_str, style="bold yellow")
    return Text(due_str)


def _render_tasks_table(tasks: list[Task], *, title: str = "Tasks") -> Table:
    today = date.today()
    table = Table(title=title, box=box.ROUNDED, show_lines=False)
    table.add_column("ID", justify="right", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Title", overflow="fold")
    table.add_column("Priority", no_wrap=True)
    table.add_column("Due", no_wrap=True)
    table.add_column("Tags", style="magenta")
    table.add_column("Repeat", style="dim", no_wrap=True)

    for task in tasks:
        if task.done:
            title_text = Text(task.title, style="dim strike")
        elif task.is_overdue(as_of=today):
            title_text = Text(task.title, style="bold red")
        else:
            title_text = Text(task.title)

        tags_text = ", ".join(task.tags) if task.tags else "-"
        repeat_text = task.recurrence.value if task.recurrence else "-"
        table.add_row(
            str(task.id),
            _status_text(task, today=today),
            title_text,
            _priority_text(task.priority),
            _due_text(task, today=today),
            tags_text,
            repeat_text,
        )
    return table


def _handle_error(exc: TodoError) -> typer.Exit:
    err_console.print(f"[bold red]Error:[/bold red] {exc}")
    return typer.Exit(code=1)


def _resolve_done_filter(pending: bool, done: bool) -> bool | None:
    if pending and done:
        raise ValidationError("Cannot combine --pending and --done filters.")
    if pending:
        return False
    if done:
        return True
    return None


@app.callback()
def main_callback(
    ctx: typer.Context,
    backend: Annotated[
        Optional[str],
        typer.Option("--backend", "-b", help="Storage backend: json or sqlite."),
    ] = None,
    db_path: Annotated[
        Optional[Path],
        typer.Option("--db", "--file", "-f", help="Path to JSON or SQLite storage file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose logging."),
    ] = False,
) -> None:
    """Configure storage backend and logging for subcommands."""
    configure_logging(verbose=verbose)
    ctx.obj = CliContext(backend=backend, db_path=db_path, verbose=verbose)


@app.command()
def add(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Title of the task.")],
    due: Annotated[
        Optional[str],
        typer.Option("--due", "-d", help="Optional due date (YYYY-MM-DD)."),
    ] = None,
    priority: Annotated[
        str,
        typer.Option("--priority", "-p", help="Task priority: low, medium, or high."),
    ] = "medium",
    tag: Annotated[
        Optional[list[str]],
        typer.Option("--tag", "-t", help="Tag(s) for the task. Repeat or comma-separate."),
    ] = None,
    repeat: Annotated[
        Optional[str],
        typer.Option("--repeat", "-r", help="Recurrence cadence: daily, weekly, or monthly."),
    ] = None,
) -> None:
    """Add a new task."""
    try:
        manager = _get_manager(ctx)
        task = manager.add(
            title,
            due=due,
            priority=priority,
            tags=tag,
            recurrence=repeat,
        )
    except TodoError as exc:
        raise _handle_error(exc) from exc

    details: list[str] = [f"priority: {task.priority.value}"]
    if task.due is not None:
        details.append(f"due: {task.due.isoformat()}")
    if task.tags:
        details.append(f"tags: {', '.join(task.tags)}")
    if task.recurrence is not None:
        details.append(f"repeat: {task.recurrence.value}")
    console.print(
        f"[green]Added[/green] [bold]#{task.id}[/bold] {task.title} "
        f"[dim]({', '.join(details)})[/dim]"
    )


@app.command("list")
def list_tasks(
    ctx: typer.Context,
    pending: Annotated[
        bool,
        typer.Option("--pending", help="Show only pending tasks."),
    ] = False,
    done: Annotated[
        bool,
        typer.Option("--done", help="Show only completed tasks."),
    ] = False,
    tag: Annotated[
        Optional[str],
        typer.Option("--tag", "-t", help="Filter by tag."),
    ] = None,
    priority: Annotated[
        Optional[str],
        typer.Option("--priority", "-p", help="Filter by priority (low, medium, high)."),
    ] = None,
    search: Annotated[
        Optional[str],
        typer.Option("--search", "-q", help="Search keyword in title or tags."),
    ] = None,
    sort_by: Annotated[
        str,
        typer.Option("--sort", "-s", help="Sort by: id, due, priority, or created."),
    ] = "id",
    desc: Annotated[
        bool,
        typer.Option("--desc", help="Reverse the sort order."),
    ] = False,
    overdue: Annotated[
        bool,
        typer.Option("--overdue", help="Show only overdue tasks."),
    ] = False,
) -> None:
    """List tasks with optional filters, sorting, and overdue highlighting."""
    try:
        done_filter = _resolve_done_filter(pending, done)
        manager = _get_manager(ctx)
        tasks = manager.list(
            done=done_filter,
            tag=tag,
            priority=priority,
            search=search,
            sort_by=sort_by,
            descending=desc,
            overdue_only=overdue,
        )
    except TodoError as exc:
        raise _handle_error(exc) from exc

    if not tasks:
        console.print("[dim]No matching tasks found.[/dim]")
        return

    today = date.today()
    overdue_count = sum(1 for task in tasks if task.is_overdue(as_of=today))
    pending_count = sum(1 for task in tasks if not task.done)
    table = _render_tasks_table(tasks)
    console.print(table)
    summary = f"Total: {len(tasks)} | Pending: {pending_count}"
    if overdue_count:
        summary += f" | [bold red]Overdue: {overdue_count}[/bold red]"
    console.print(summary)


@app.command()
def done(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(help="Stable ID of the task to complete.")],
) -> None:
    """Mark a task as completed (and schedule the next occurrence if recurring)."""
    try:
        manager = _get_manager(ctx)
        result = manager.complete_detailed(task_id)
    except TodoError as exc:
        raise _handle_error(exc) from exc

    console.print(
        f"[green]Completed[/green] [bold]#{result.completed_task.id}[/bold] "
        f"{result.completed_task.title}"
    )
    if result.next_task is not None:
        due_str = result.next_task.due.isoformat() if result.next_task.due else "unscheduled"
        recurrence_label = (
            result.next_task.recurrence.value if result.next_task.recurrence else "recurring"
        )
        console.print(
            f"[cyan]Scheduled next {recurrence_label} occurrence[/cyan] "
            f"[bold]#{result.next_task.id}[/bold] (due {due_str})"
        )


@app.command()
def reopen(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(help="ID of the completed task to reopen.")],
) -> None:
    """Mark a completed task back to pending."""
    try:
        manager = _get_manager(ctx)
        task = manager.reopen(task_id)
    except TodoError as exc:
        raise _handle_error(exc) from exc
    console.print(f"[cyan]Reopened[/cyan] [bold]#{task.id}[/bold] {task.title}")


@app.command()
def edit(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(help="ID of the task to update.")],
    title: Annotated[Optional[str], typer.Option("--title", help="New task title.")] = None,
    due: Annotated[Optional[str], typer.Option("--due", "-d", help="New due date (YYYY-MM-DD).")] = None,
    clear_due: Annotated[bool, typer.Option("--clear-due", help="Remove due date.")] = False,
    priority: Annotated[
        Optional[str],
        typer.Option("--priority", "-p", help="New priority: low, medium, or high."),
    ] = None,
    tag: Annotated[
        Optional[list[str]],
        typer.Option("--tag", "-t", help="Replace tags with the provided tag(s)."),
    ] = None,
    clear_tags: Annotated[bool, typer.Option("--clear-tags", help="Remove all tags.")] = False,
    repeat: Annotated[
        Optional[str],
        typer.Option("--repeat", "-r", help="New recurrence: daily, weekly, or monthly."),
    ] = None,
    clear_repeat: Annotated[
        bool,
        typer.Option("--clear-repeat", help="Disable recurrence."),
    ] = False,
) -> None:
    """Edit metadata on an existing task."""
    try:
        manager = _get_manager(ctx)
        kwargs: dict[str, object] = {}
        if title is not None:
            kwargs["title"] = title
        if clear_due:
            kwargs["due"] = None
        elif due is not None:
            kwargs["due"] = due
        if priority is not None:
            kwargs["priority"] = priority
        if clear_tags:
            kwargs["tags"] = []
        elif tag is not None:
            kwargs["tags"] = tag
        if clear_repeat:
            kwargs["recurrence"] = None
        elif repeat is not None:
            kwargs["recurrence"] = repeat
        if not kwargs:
            raise ValidationError("Provide at least one field to update.")
        updated = manager.update(task_id, **kwargs)  # type: ignore[arg-type]
    except TodoError as exc:
        raise _handle_error(exc) from exc
    console.print(f"[green]Updated[/green] [bold]#{updated.id}[/bold] {updated.title}")


@app.command()
def delete(
    ctx: typer.Context,
    task_id: Annotated[int, typer.Argument(help="Stable ID of the task to delete.")],
) -> None:
    """Delete a task by its ID."""
    try:
        manager = _get_manager(ctx)
        removed = manager.delete(task_id)
    except TodoError as exc:
        raise _handle_error(exc) from exc
    console.print(f"[yellow]Deleted[/yellow] [bold]#{removed.id}[/bold] {removed.title}")


@app.command()
def clear(ctx: typer.Context) -> None:
    """Delete all completed tasks."""
    try:
        manager = _get_manager(ctx)
        removed = manager.clear_completed()
    except TodoError as exc:
        raise _handle_error(exc) from exc
    if not removed:
        console.print("[dim]No completed tasks to clear.[/dim]")
        return
    console.print(f"[yellow]Cleared {len(removed)} completed task(s).[/yellow]")


@app.command()
def undo(ctx: typer.Context) -> None:
    """Undo the most recent task mutation."""
    try:
        manager = _get_manager(ctx)
        action = manager.undo()
    except TodoError as exc:
        raise _handle_error(exc) from exc
    console.print(f"[cyan]Undid:[/cyan] {action}")


@app.command()
def reminders(
    ctx: typer.Context,
    days: Annotated[
        int,
        typer.Option("--days", "-d", help="Show upcoming tasks due within N days."),
    ] = 3,
) -> None:
    """Show overdue, due-today, and upcoming task reminders."""
    try:
        manager = _get_manager(ctx)
        report = manager.reminders(within_days=days)
    except TodoError as exc:
        raise _handle_error(exc) from exc

    if report.total_count == 0:
        console.print(
            Panel(
                f"No overdue tasks or tasks due within the next {days} day(s).",
                title="Reminders",
                border_style="green",
            )
        )
        return

    if report.overdue:
        console.print(_render_tasks_table(report.overdue, title="Overdue"))
    if report.due_today:
        console.print(_render_tasks_table(report.due_today, title="Due Today"))
    if report.upcoming:
        console.print(
            _render_tasks_table(report.upcoming, title=f"Upcoming (Next {days} Day(s))")
        )


@app.command()
def export(
    ctx: typer.Context,
    format: Annotated[
        str,
        typer.Option("--format", "-F", help="Export format: csv or markdown."),
    ] = "markdown",
    style: Annotated[
        str,
        typer.Option("--style", help="Markdown style: checklist or table."),
    ] = "checklist",
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Write exported output to a file instead of stdout."),
    ] = None,
    pending: Annotated[bool, typer.Option("--pending", help="Export only pending tasks.")] = False,
    done_only: Annotated[bool, typer.Option("--done", help="Export only completed tasks.")] = False,
    tag: Annotated[Optional[str], typer.Option("--tag", "-t", help="Filter by tag.")] = None,
    sort_by: Annotated[str, typer.Option("--sort", "-s", help="Sort field: id, due, priority, created.")] = "id",
) -> None:
    """Export tasks as CSV or Markdown."""
    try:
        done_filter = _resolve_done_filter(pending, done_only)
        manager = _get_manager(ctx)
        tasks = manager.list(done=done_filter, tag=tag, sort_by=sort_by)
        normalized_format = format.strip().lower()
        if normalized_format == "csv":
            rendered = manager.export_csv(tasks)
        elif normalized_format in {"markdown", "md"}:
            rendered = manager.export_markdown(tasks, style=style)
        else:
            raise ValidationError(
                f"Unsupported export format {format!r}. Expected 'csv' or 'markdown'."
            )
        if output is not None:
            _atomic_write_text(output, rendered)
    except TodoError as exc:
        raise _handle_error(exc) from exc

    if output is not None:
        console.print(f"[green]Exported[/green] {len(tasks)} task(s) to {output}")
    else:
        typer.echo(rendered, nl=False)


@app.command()
def config(
    ctx: typer.Context,
    backend: Annotated[
        Optional[str],
        typer.Option("--backend", "-b", help="Persist default backend (json or sqlite)."),
    ] = None,
    path: Annotated[
        Optional[Path],
        typer.Option("--path", "--db", help="Persist default storage file path."),
    ] = None,
) -> None:
    """View or update persistent storage configuration."""
    try:
        if backend is not None:
            saved_path = save_user_config(backend=backend, path=path)
            console.print(f"[green]Saved configuration[/green] to {saved_path}")
            return
        if path is not None:
            current = resolve_config()
            saved_path = save_user_config(backend=current.backend, path=path)
            console.print(f"[green]Saved configuration[/green] to {saved_path}")
            return
        state = ctx.obj if isinstance(ctx.obj, CliContext) else None
        effective = resolve_config(
            backend=state.backend if state else None,
            path=state.db_path if state else None,
        )
    except TodoError as exc:
        raise _handle_error(exc) from exc

    console.print(
        Panel(
            f"Backend: [bold]{effective.backend}[/bold]\n"
            f"Storage path: [bold]{effective.path}[/bold]\n"
            f"Config file: {DEFAULT_CONFIG_PATH}",
            title="Todo Configuration",
        )
    )


@app.command()
def menu(ctx: typer.Context) -> None:
    """Launch the interactive numbered menu backed by TaskManager."""
    try:
        manager = _get_manager(ctx)
    except TodoError as exc:
        raise _handle_error(exc) from exc
    run_menu(manager)


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option("--host", help="Bind host for the FastAPI server.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port for the FastAPI server.")] = 8000,
) -> None:
    """Start the FastAPI REST server over TaskManager."""
    try:
        import uvicorn
    except ImportError as exc:
        err_console.print(
            "[bold red]Error:[/bold red] uvicorn/fastapi are not installed. "
            "Install with: pip install -e ."
        )
        raise typer.Exit(code=1) from exc

    try:
        manager = _get_manager(ctx)
        from todo.api import create_app

        api_app = create_app(manager=manager)
    except TodoError as exc:
        raise _handle_error(exc) from exc

    console.print(f"[green]Starting Todo API[/green] at http://{host}:{port}")
    uvicorn.run(api_app, host=host, port=port)


def main() -> None:
    """CLI script entrypoint."""
    app()


if __name__ == "__main__":
    main()
