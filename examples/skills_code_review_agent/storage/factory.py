"""Select a review store from environment-backed configuration."""

import os
from pathlib import Path

from .base import BaseReviewStore
from .sqlite import SCHEMA_PATH
from .sqlite import SQLiteReviewStore

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = EXAMPLE_ROOT / "storage" / "reviews.sqlite3"


def create_review_store(database_path: Path | None = None) -> BaseReviewStore:
    """Create the configured persistence implementation.

    An explicit CLI path overrides ``CODE_REVIEW_SQLITE_PATH``. The backend is
    selected with ``CODE_REVIEW_STORAGE_BACKEND`` and currently supports only
    ``sqlite``.
    """
    backend = os.getenv("CODE_REVIEW_STORAGE_BACKEND", "sqlite").strip().lower()
    if backend != "sqlite":
        raise ValueError(f"Unsupported storage backend: {backend}")

    if database_path is not None:
        sqlite_path = database_path
    else:
        configured_path = os.getenv("CODE_REVIEW_SQLITE_PATH", "").strip()
        sqlite_path = Path(configured_path) if configured_path else DEFAULT_SQLITE_PATH
        if configured_path and not sqlite_path.is_absolute():
            sqlite_path = EXAMPLE_ROOT / sqlite_path
    configured_schema = os.getenv("CODE_REVIEW_SQLITE_SCHEMA_PATH", "").strip()
    schema_path = Path(configured_schema) if configured_schema else SCHEMA_PATH
    if configured_schema and not schema_path.is_absolute():
        schema_path = EXAMPLE_ROOT / schema_path
    return SQLiteReviewStore(sqlite_path, schema_path=schema_path)
