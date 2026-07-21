# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Monitoring and audit layer for the code review agent.

Records per-review telemetry including:
- Total duration and sandbox execution duration
- Tool call and filter intercept counts
- Finding count and severity distribution
- Exception type distribution

All metrics are persisted to the monitor_summary table in the database.
"""

from __future__ import annotations

import json
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .db.storage import StorageABC
from .models import Finding, MonitorSummary, ReviewTask


# ──────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────


@dataclass
class ReviewMetrics:
    """Collected metrics for a single review session."""

    # Timing
    total_duration_ms: float = 0.0
    sandbox_duration_ms: float = 0.0
    parse_duration_ms: float = 0.0
    filter_duration_ms: float = 0.0

    # Counters
    tool_call_count: int = 0
    intercept_count: int = 0
    finding_count: int = 0

    # Distributions
    severity_counts: dict[str, int] = field(default_factory=dict)
    exception_types: list[str] = field(default_factory=list)

    # Errors
    errors: list[str] = field(default_factory=list)

    def to_monitor_summary(self, task_id: str) -> MonitorSummary:
        """Convert metrics to a MonitorSummary model for DB persistence."""
        return MonitorSummary(
            task_id=task_id,
            total_duration_ms=self.total_duration_ms,
            sandbox_duration_ms=self.sandbox_duration_ms,
            tool_call_count=self.tool_call_count,
            intercept_count=self.intercept_count,
            finding_count=self.finding_count,
            severity_distribution=json.dumps(self.severity_counts, ensure_ascii=False),
            exception_types=json.dumps(self.exception_types, ensure_ascii=False),
        )


# ──────────────────────────────────────────────
# 7.1 + 7.2: Monitor
# ──────────────────────────────────────────────


class ReviewMonitor:
    """Collects and persists review telemetry.

    Usage:
        monitor = ReviewMonitor(storage, task_id)
        monitor.start()
        # ... run review ...
        monitor.record_sandbox(duration_ms=1500)
        monitor.record_tool_call()
        monitor.record_findings(findings)
        monitor.record_exception(e)
        monitor.finish()  # saves to DB
    """

    def __init__(self, storage: StorageABC, task_id: str):
        self.storage = storage
        self.task_id = task_id
        self.metrics = ReviewMetrics()
        self._start_time: Optional[float] = None

    # ── Timing ──

    def start(self) -> None:
        """Start the review timer."""
        self._start_time = time.monotonic()

    def finish(self) -> MonitorSummary:
        """Stop the timer and persist metrics to the database.

        Returns:
            The saved MonitorSummary.
        """
        if self._start_time is not None:
            self.metrics.total_duration_ms = (time.monotonic() - self._start_time) * 1000

        summary = self.metrics.to_monitor_summary(self.task_id)
        self.storage.save_monitor_summary(summary)
        return summary

    # ── Individual recorders ──

    def record_sandbox_duration(self, duration_ms: float) -> None:
        """Record sandbox execution duration."""
        self.metrics.sandbox_duration_ms += duration_ms

    def record_parse_duration(self, duration_ms: float) -> None:
        """Record diff parsing duration."""
        self.metrics.parse_duration_ms += duration_ms

    def record_filter_duration(self, duration_ms: float) -> None:
        """Record filter evaluation duration."""
        self.metrics.filter_duration_ms += duration_ms

    def record_tool_call(self) -> None:
        """Increment the tool call counter."""
        self.metrics.tool_call_count += 1

    def record_intercept(self) -> None:
        """Increment the filter intercept counter."""
        self.metrics.intercept_count += 1

    def record_findings(
        self,
        findings: list[Finding],
        warnings: Optional[list[Finding]] = None,
        needs_review: Optional[list[Finding]] = None,
    ) -> None:
        """Record findings and compute severity distribution.

        Args:
            findings: High-confidence findings.
            warnings: Medium-confidence findings (optional).
            needs_review: Low-confidence findings (optional).
        """
        all_findings = list(findings)
        if warnings:
            all_findings.extend(warnings)
        if needs_review:
            all_findings.extend(needs_review)

        self.metrics.finding_count = len(all_findings)

        # Severity distribution
        severity_counts: Counter = Counter()
        for f in all_findings:
            severity_counts[f.severity.value] += 1
        self.metrics.severity_counts = dict(severity_counts)

    def record_exception(self, exception: Exception) -> None:
        """Record an exception type for the audit log.

        Args:
            exception: The exception that occurred.
        """
        exc_type = type(exception).__name__
        if exc_type not in self.metrics.exception_types:
            self.metrics.exception_types.append(exc_type)
        self.metrics.errors.append(f"{exc_type}: {str(exception)[:200]}")

    # ── Batch / convenience ──

    def update_from_task(self, task: ReviewTask) -> None:
        """Update metrics from a completed ReviewTask."""
        if task.total_duration_ms is not None:
            self.metrics.total_duration_ms = task.total_duration_ms
        if task.error_message:
            self.metrics.errors.append(task.error_message)


# ──────────────────────────────────────────────
# 7.3: DB persistence helper
# ──────────────────────────────────────────────


def save_monitor_summary(
    storage: StorageABC,
    task_id: str,
    total_duration_ms: float = 0.0,
    sandbox_duration_ms: float = 0.0,
    tool_call_count: int = 0,
    intercept_count: int = 0,
    finding_count: int = 0,
    severity_distribution: Optional[dict[str, int]] = None,
    exception_types: Optional[list[str]] = None,
) -> MonitorSummary:
    """Create and save a MonitorSummary to the database.

    This is a convenience function for one-off saves without creating
    a full ReviewMonitor instance.

    Args:
        storage: Storage backend.
        task_id: Review task ID.
        total_duration_ms: Total review duration in ms.
        sandbox_duration_ms: Total sandbox duration in ms.
        tool_call_count: Number of tool calls.
        intercept_count: Number of filter intercepts.
        finding_count: Total number of findings.
        severity_distribution: Dict of severity → count.
        exception_types: List of exception type names.

    Returns:
        The saved MonitorSummary.
    """
    summary = MonitorSummary(
        task_id=task_id,
        total_duration_ms=total_duration_ms,
        sandbox_duration_ms=sandbox_duration_ms,
        tool_call_count=tool_call_count,
        intercept_count=intercept_count,
        finding_count=finding_count,
        severity_distribution=json.dumps(severity_distribution or {}, ensure_ascii=False),
        exception_types=json.dumps(exception_types or [], ensure_ascii=False),
    )
    return storage.save_monitor_summary(summary)


def get_monitor_summary(storage: StorageABC, task_id: str) -> Optional[MonitorSummary]:
    """Retrieve a MonitorSummary from the database."""
    return storage.get_monitor_summary(task_id)