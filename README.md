# Advanced CLI To-Do Manager

A packaged, tested command-line task manager built with clean layered architecture:

- **Domain Models** (`src/todo/models.py`): `Task` dataclass, `Priority`, `Recurrence`, date/tag validation.
- **Swappable Storage** (`src/todo/storage.py`): Abstract `Storage` interface with `JsonStorage` (atomic temp-file + replace writes, corruption detection) and `SqliteStorage` (WAL transactions + relational schema).
- **Business Logic** (`src/todo/manager.py`): `TaskManager` with filtering, sorting, recurring task scheduling, persistent undo history, due reminders, and CSV/Markdown export.
- **CLI & Rich UI** (`src/todo/cli.py`): `typer` commands with colorized `rich` tables and overdue highlighting.
- **Interactive Menu** (`src/todo/menu.py`): Classic numbered menu backed by `TaskManager`.
- **REST API** (`src/todo/api.py`): FastAPI wrapper over `TaskManager`.

## Installation

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -e ".[dev]"
```

## Quick Start

```bash
# Add tasks with due dates, priority, tags, and optional recurrence
todo add "buy milk" --due 2026-12-01 --priority high --tag errands
todo add "Weekly team sync" --due 2026-12-02 --priority medium --tag work --repeat weekly

# List, filter, and sort tasks
todo list
todo list --pending --sort priority
todo list --tag work --sort due
todo list --search milk
todo list --overdue

# Complete, reopen, edit, delete, and undo
todo done 1
todo edit 2 --priority high --due 2026-12-05
todo reopen 1
todo delete 1
todo undo
todo clear

# Reminders & export
todo reminders --days 7
todo export --format csv --output tasks.csv
todo export --format markdown --style table

# Classic interactive menu (MVP 1)
todo menu
```

## Storage Backends (JSON & SQLite)

Use `--backend` / `--db` on any command, environment variables (`TODO_STORAGE`, `TODO_PATH`), or save a default in `~/.todo/config.toml`:

```bash
# Override per command
todo --backend sqlite --db tasks.db list
todo --backend json --db my_tasks.json add "Read docs"

# Persist default backend in ~/.todo/config.toml
todo config --backend sqlite --path tasks.db
todo config
```

## REST API (FastAPI)

Start the API server:

```bash
todo serve --host 127.0.0.1 --port 8000
# or with uvicorn directly:
uvicorn todo.api:app --reload
```

Available endpoints:
- `GET /health`
- `GET /tasks`, `POST /tasks`, `GET /tasks/{id}`, `PATCH /tasks/{id}`, `DELETE /tasks/{id}`
- `POST /tasks/{id}/complete`
- `GET /reminders?within_days=3`
- `POST /undo`
- `GET /export?format=csv|markdown&style=checklist|table`

## Running Tests

```bash
pytest -v
```
