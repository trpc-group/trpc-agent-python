# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Backend-swappable store interface (issue requirement 5).

The pipeline talks ONLY to this ABC. :class:`SqlReviewStore` implements it on
the SDK's ``SqlStorage`` (SQLite by default; MySQL/PostgreSQL by URL swap).
A non-SQL backend (e.g. document store) just implements this same interface.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


class ReviewStore(ABC):
    """Async persistence interface for review tasks and their artifacts."""

    @abstractmethod
    async def initialize(self) -> None:
        """Create/migrate the schema (idempotent)."""

    @abstractmethod
    async def close(self) -> None:
        """Release connections."""

    # -- writes -------------------------------------------------------------

    @abstractmethod
    async def create_task(self, task_id: str, input_type: str, input_ref: str,
                          config: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    async def update_task(self, task_id: str, **fields: Any) -> None:
        """Update mutable task fields (status, diff_summary, error_*)."""

    @abstractmethod
    async def add_sandbox_run(self, run: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    async def add_filter_event(self, event: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    async def add_findings(self, task_id: str, findings: List[Dict[str, Any]]) -> None:
        ...

    @abstractmethod
    async def save_report(self, task_id: str, report_row: Dict[str, Any]) -> None:
        ...

    # -- queries (acceptance criterion 3: everything by task id) -------------

    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    async def get_sandbox_runs(self, task_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    async def get_filter_events(self, task_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    async def get_findings(self, task_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    async def get_report(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    async def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        ...

    async def get_task_bundle(self, task_id: str) -> Dict[str, Any]:
        """Everything recorded for one task — powers ``run_agent.py show``."""
        return {
            "task": await self.get_task(task_id),
            "sandbox_runs": await self.get_sandbox_runs(task_id),
            "filter_events": await self.get_filter_events(task_id),
            "findings": await self.get_findings(task_id),
            "report": await self.get_report(task_id),
        }
