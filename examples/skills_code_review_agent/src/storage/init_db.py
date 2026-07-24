"""Database initialization helpers for the skills code review example."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import ALL_TABLES


def initialize_database(db_path: str | Path) -> Path:
    """Create all SQLite tables needed by the review pipeline."""

    resolved = Path(db_path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(resolved)
    try:
        for table in ALL_TABLES:
            connection.execute(table.ddl)
        connection.commit()
    finally:
        connection.close()
    return resolved
