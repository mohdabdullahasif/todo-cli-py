"""Logging setup for the Todo application."""

from __future__ import annotations

import logging
import os


def configure_logging(*, verbose: bool = False, level: str | None = None) -> None:
    """Configure package logging without disrupting normal CLI output unless enabled."""
    raw_level = "DEBUG" if verbose else (level or os.environ.get("TODO_LOG_LEVEL", "WARNING"))
    numeric_level = getattr(logging, raw_level.strip().upper(), logging.WARNING)
    package_logger = logging.getLogger("todo")
    package_logger.setLevel(numeric_level)
    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        package_logger.addHandler(handler)
    package_logger.propagate = False
