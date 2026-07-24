# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Database initialization for the code review agent.

Provides the init_db() function used by tests and CLI tools.
Delegates to SqliteCrRepository for actual table creation.
"""

from __future__ import annotations

from pathlib import Path


def init_db(db_path: str) -> None:
    """Initialize the SQLite database by creating all tables.

    Args:
        db_path: Path to the SQLite database file.
    """
    from storage.sqlite_repository import SqliteCrRepository
    repo = SqliteCrRepository(db_path, auto_init=True)
    repo.close()