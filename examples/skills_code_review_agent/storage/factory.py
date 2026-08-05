"""Select a review store from environment-backed configuration."""

import os
from pathlib import Path

from .base import BaseReviewStore
from .postgresql import SCHEMA_PATH as POSTGRES_SCHEMA_PATH
from .postgresql import PostgreSQLReviewStore
from .sqlite import SCHEMA_PATH
from .sqlite import SQLiteReviewStore

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = EXAMPLE_ROOT / "storage" / "reviews.sqlite3"


def _configured_path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    path = Path(value) if value else default
    if value and not path.is_absolute():
        path = EXAMPLE_ROOT / path
    return path


def _bounded_integer(name: str, default: int, maximum: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return parsed


def create_review_store(database_path: Path | None = None) -> BaseReviewStore:
    """Create the configured persistence implementation.

    An explicit CLI path overrides ``CODE_REVIEW_SQLITE_PATH``. PostgreSQL is
    selected entirely through environment configuration so a DSN never appears
    in command-line process listings.
    """
    backend = os.getenv("CODE_REVIEW_STORAGE_BACKEND", "sqlite").strip().lower()
    if backend in {"postgres", "postgresql"}:
        if database_path is not None:
            raise ValueError("--database can only be used with SQLite storage")
        return PostgreSQLReviewStore(
            os.getenv("CODE_REVIEW_POSTGRES_DSN", ""),
            schema_path=_configured_path(
                "CODE_REVIEW_POSTGRES_SCHEMA_PATH",
                POSTGRES_SCHEMA_PATH,
            ),
            connect_timeout_seconds=_bounded_integer(
                "CODE_REVIEW_POSTGRES_CONNECT_TIMEOUT_SECONDS",
                5,
                30,
            ),
            statement_timeout_seconds=_bounded_integer(
                "CODE_REVIEW_POSTGRES_STATEMENT_TIMEOUT_SECONDS",
                15,
                60,
            ),
        )
    if backend != "sqlite":
        raise ValueError(f"Unsupported storage backend: {backend}")

    if database_path is not None:
        sqlite_path = database_path
    else:
        sqlite_path = _configured_path("CODE_REVIEW_SQLITE_PATH", DEFAULT_SQLITE_PATH)
    schema_path = _configured_path("CODE_REVIEW_SQLITE_SCHEMA_PATH", SCHEMA_PATH)
    return SQLiteReviewStore(sqlite_path, schema_path=schema_path)
