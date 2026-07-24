# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Backward-compatible storage wrapper for the code review agent.

Provides a SqliteStorage class that the test file imports.
Delegates to SqliteCrRepository from the storage package.
"""

from __future__ import annotations

from typing import Any, Optional

from storage.sqlite_repository import SqliteCrRepository


class SqliteStorage:
    """Backward-compatible wrapper around SqliteCrRepository.

    Used by evals/test_cr_agent.py and other legacy consumers.
    Provides a simplified interface for basic CRUD operations.
    """

    def __init__(self, db_path: str) -> None:
        self._repo = SqliteCrRepository(db_path)

    @property
    def repo(self) -> SqliteCrRepository:
        return self._repo

    def get_task_count(self) -> int:
        """Get the total number of review tasks."""
        return len(self._repo.list_tasks(limit=10000))

    def get_finding_count(self, task_id: str) -> int:
        """Get the number of findings for a task."""
        return self._repo.count_findings_by_task(task_id)

    def close(self) -> None:
        self._repo.close()