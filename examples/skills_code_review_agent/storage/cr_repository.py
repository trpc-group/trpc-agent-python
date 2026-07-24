# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Abstract repository interface for the code review agent.

Defines the storage contract that all concrete implementations must follow.
The default implementation is SQLite (SqliteCrRepository), but this ABC
allows switching to PostgreSQL, MySQL, or other backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .models import (
    FilterLog,
    Finding,
    MonitorSummary,
    ReviewReport,
    ReviewTask,
    SandboxRun,
)


class CrRepository(ABC):
    """Abstract base class for review data storage.

    All storage operations for the code review pipeline go through this
    interface. The default implementation is SqliteCrRepository.
    """

    # ── Review Tasks ──

    @abstractmethod
    def create_task(self, task: ReviewTask) -> ReviewTask:
        """Insert a new review task."""
        ...

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[ReviewTask]:
        """Get a review task by ID."""
        ...

    @abstractmethod
    def update_task(self, task: ReviewTask) -> None:
        """Update an existing review task."""
        ...

    @abstractmethod
    def list_tasks(self, limit: int = 20, offset: int = 0) -> list[ReviewTask]:
        """List review tasks, newest first."""
        ...

    # ── Findings ──

    @abstractmethod
    def create_finding(self, finding: Finding) -> Finding:
        """Insert a new finding."""
        ...

    @abstractmethod
    def get_findings_by_task(self, task_id: str) -> list[Finding]:
        """Get all findings for a task."""
        ...

    @abstractmethod
    def is_duplicate_finding(self, dedup_key: str, task_id: str) -> bool:
        """Check if a finding with the same dedup_key already exists in the task."""
        ...

    @abstractmethod
    def count_findings_by_task(self, task_id: str) -> int:
        """Count findings for a task."""
        ...

    # ── Sandbox Runs ──

    @abstractmethod
    def create_sandbox_run(self, run: SandboxRun) -> SandboxRun:
        """Insert a new sandbox execution record."""
        ...

    @abstractmethod
    def get_sandbox_runs_by_task(self, task_id: str) -> list[SandboxRun]:
        """Get all sandbox runs for a task."""
        ...

    # ── Reports ──

    @abstractmethod
    def create_report(self, report: ReviewReport) -> ReviewReport:
        """Insert a new review report."""
        ...

    @abstractmethod
    def get_reports_by_task(self, task_id: str) -> list[ReviewReport]:
        """Get all reports for a task."""
        ...

    # ── Filter Logs ──

    @abstractmethod
    def create_filter_log(self, log: FilterLog) -> FilterLog:
        """Insert a new filter log entry."""
        ...

    @abstractmethod
    def get_filter_logs_by_task(self, task_id: str) -> list[FilterLog]:
        """Get all filter logs for a task."""
        ...

    # ── Monitor Summary ──

    @abstractmethod
    def create_monitor_summary(self, summary: MonitorSummary) -> MonitorSummary:
        """Insert a new monitor summary."""
        ...

    @abstractmethod
    def get_monitor_summary(self, task_id: str) -> Optional[MonitorSummary]:
        """Get the monitor summary for a task."""
        ...

    # ── Lifecycle ──

    @abstractmethod
    def close(self) -> None:
        """Close the repository and release resources."""
        ...