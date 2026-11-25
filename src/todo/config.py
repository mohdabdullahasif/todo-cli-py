"""Configuration resolution and storage factory helpers."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from todo.exceptions import ValidationError
from todo.storage import JsonStorage, SqliteStorage, Storage

VALID_BACKENDS = {"json", "sqlite"}
DEFAULT_CONFIG_PATH = Path.home() / ".todo" / "config.toml"
DEFAULT_PATHS: dict[str, str] = {
    "json": "tasks.json",
    "sqlite": "tasks.db",
}


@dataclass(frozen=True, slots=True)
class TodoConfig:
    """Resolved storage configuration."""

    backend: str = "json"
    path: Path = Path("tasks.json")


def _read_config_file(config_path: Path) -> dict[str, str]:
    if not config_path.exists():
        return {}
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValidationError(f"Invalid config file '{config_path}': {exc}") from exc

    storage_section = raw.get("storage", raw)
    if not isinstance(storage_section, dict):
        return {}
    result: dict[str, str] = {}
    if isinstance(storage_section.get("backend"), str):
        result["backend"] = storage_section["backend"]
    if isinstance(storage_section.get("path"), str):
        result["path"] = storage_section["path"]
    return result


def resolve_config(
    *,
    backend: str | None = None,
    path: str | Path | None = None,
    config_path: Path | None = None,
) -> TodoConfig:
    """Resolve storage backend and path from CLI flags, env vars, config file, and defaults."""
    cfg_file = config_path or Path(os.environ.get("TODO_CONFIG", str(DEFAULT_CONFIG_PATH)))
    file_settings = _read_config_file(cfg_file)

    env_backend = os.environ.get("TODO_STORAGE")
    env_path = os.environ.get("TODO_PATH") or os.environ.get("TODO_DB")

    chosen_backend = (backend or env_backend or file_settings.get("backend") or "json").strip().lower()
    if chosen_backend not in VALID_BACKENDS:
        raise ValidationError(
            f"Unknown storage backend {chosen_backend!r}. Expected 'json' or 'sqlite'."
        )

    explicit_path = path or env_path
    if explicit_path is not None:
        chosen_path = Path(explicit_path)
    elif file_settings.get("path") and (backend is None and env_backend is None or chosen_backend == file_settings.get("backend")):
        chosen_path = Path(file_settings["path"])
    else:
        chosen_path = Path(DEFAULT_PATHS[chosen_backend])

    return TodoConfig(backend=chosen_backend, path=chosen_path)


def save_user_config(
    *,
    backend: str,
    path: str | Path | None = None,
    config_path: Path | None = None,
) -> Path:
    """Persist default storage backend/path to the user's TOML config file."""
    normalized_backend = backend.strip().lower()
    if normalized_backend not in VALID_BACKENDS:
        raise ValidationError(
            f"Unknown storage backend {backend!r}. Expected 'json' or 'sqlite'."
        )
    target = config_path or Path(os.environ.get("TODO_CONFIG", str(DEFAULT_CONFIG_PATH)))
    target.parent.mkdir(parents=True, exist_ok=True)
    resolved_path = str(path) if path is not None else DEFAULT_PATHS[normalized_backend]
    escaped_path = resolved_path.replace("\\", "\\\\").replace('"', '\\"')
    content = (
        "[storage]\n"
        f'backend = "{normalized_backend}"\n'
        f'path = "{escaped_path}"\n'
    )
    target.write_text(content, encoding="utf-8")
    return target


def create_storage(
    *,
    backend: str | None = None,
    path: str | Path | None = None,
    config_path: Path | None = None,
) -> Storage:
    """Instantiate the configured Storage implementation."""
    config = resolve_config(backend=backend, path=path, config_path=config_path)
    if config.backend == "sqlite":
        return SqliteStorage(config.path)
    return JsonStorage(config.path)
