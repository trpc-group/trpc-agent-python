# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit monitoring for the code review agent.

Records metrics for each review run: total duration, sandbox duration,
tool call counts, intercept counts, finding counts, severity distribution,
and exception types. All metrics are persisted to the monitor_summary table.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from storage.models import MonitorSummary


@dataclass
class AuditCollector:
    """Collects monitoring metrics during a review run.

    Usage:
        collector = AuditCollector(task_id="uuid")
        collector.start()
        # ... do work ...
        collector.record_sandbox_duration(3200)
        collector.record_tool_call()
        collector.record_finding_count(3)
        summary = collector.build()
    """

    task_id: str = ""
    _start_time: Optional[float] = None
    total_duration_ms: float = 0.0
    sandbox_duration_ms: float = 0.0
    tool_call_count: int = 0
    intercept_count: int = 0
    finding_count: int = 0
    severity_distribution: dict[str, int] = field(default_factory=lambda: {
        "critical": 0, "warning": 0, "suggestion": 0,
    })
    exception_types: list[str] = field(default_factory=list)
    filter_intercepts: list[dict[str, Any]] = field(default_factory=list)

    def start(self) -> None:
        """Start the timer."""
        self._start_time = time.time()

    def stop(self) -> None:
        """Stop the timer and record total duration."""
        if self._start_time is not None:
            self.total_duration_ms = (time.time() - self._start_time) * 1000

    def record_sandbox_duration(self, ms: float) -> None:
        """Record sandbox execution duration."""
        self.sandbox_duration_ms += ms

    def record_tool_call(self) -> None:
        """Increment tool call counter."""
        self.tool_call_count += 1

    def record_intercept(self) -> None:
        """Increment intercept counter."""
        self.intercept_count += 1

    def record_finding_count(self, count: int) -> None:
        """Record total finding count."""
        self.finding_count = count

    def record_severity(self, severity: str, count: int = 1) -> None:
        """Record a finding severity entry."""
        if severity in self.severity_distribution:
            self.severity_distribution[severity] += count
        else:
            self.severity_distribution[severity] = count

    def record_exception(self, exc_type: str) -> None:
        """Record an exception type."""
        if exc_type not in self.exception_types:
            self.exception_types.append(exc_type)

    def record_filter_intercept(self, intercept: dict[str, Any]) -> None:
        """Record a filter intercept event."""
        self.filter_intercepts.append(intercept)

    def build(self) -> MonitorSummary:
        """Build and return a MonitorSummary from collected data.

        Returns:
            A MonitorSummary model ready for database storage.
        """
        return MonitorSummary(
            task_id=self.task_id,
            total_duration_ms=self.total_duration_ms,
            sandbox_duration_ms=self.sandbox_duration_ms,
            tool_call_count=self.tool_call_count,
            intercept_count=self.intercept_count,
            finding_count=self.finding_count,
            severity_distribution=json.dumps(self.severity_distribution, ensure_ascii=False),
            exception_types=json.dumps(self.exception_types, ensure_ascii=False),
            filter_intercepts=json.dumps(self.filter_intercepts, ensure_ascii=False),
        )


def create_audit_record(
    task_id: str,
    duration_ms: float,
    sandbox_duration_ms: float = 0.0,
    tool_call_count: int = 0,
    intercept_count: int = 0,
    finding_count: int = 0,
    severity_dist: Optional[dict[str, int]] = None,
    exception_types: Optional[list[str]] = None,
    filter_intercepts: Optional[list[dict[str, Any]]] = None,
) -> MonitorSummary:
    """Create an audit record directly from given values.

    Convenience function for one-shot audit record creation.

    Args:
        task_id: The review task ID.
        duration_ms: Total pipeline duration in ms.
        sandbox_duration_ms: Sandbox execution duration in ms.
        tool_call_count: Number of tool calls made.
        intercept_count: Number of filter intercepts.
        finding_count: Total number of findings.
        severity_dist: Dict of severity -> count.
        exception_types: List of exception type names.
        filter_intercepts: List of filter intercept dicts.

    Returns:
        A MonitorSummary model.
    """
    return MonitorSummary(
        task_id=task_id,
        total_duration_ms=duration_ms,
        sandbox_duration_ms=sandbox_duration_ms,
        tool_call_count=tool_call_count,
        intercept_count=intercept_count,
        finding_count=finding_count,
        severity_distribution=json.dumps(severity_dist or {}, ensure_ascii=False),
        exception_types=json.dumps(exception_types or [], ensure_ascii=False),
        filter_intercepts=json.dumps(filter_intercepts or [], ensure_ascii=False),
    )