"""In-process, serialization-safe review metrics."""

from __future__ import annotations

import time
from collections import Counter, defaultdict


class MetricsCollector:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.stage_duration_ms: dict[str, int] = defaultdict(int)
        self.tool_calls = 0
        self.blocked_executions = 0
        self.finding_severity: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()

    def record_stage(self, stage: str, duration_seconds: float) -> None:
        self.stage_duration_ms[stage] += round(duration_seconds * 1000)

    def record_tool(self, *, blocked: bool = False) -> None:
        self.tool_calls += 1
        self.blocked_executions += int(blocked)

    def record_finding(self, severity: str) -> None:
        self.finding_severity[severity] += 1

    def record_error(self, error: BaseException | str) -> None:
        self.errors[type(error).__name__ if isinstance(error, BaseException) else error] += 1

    def snapshot(self) -> dict[str, object]:
        return {
            "total_duration_ms": round((time.monotonic() - self.started) * 1000),
            "stage_duration_ms": dict(self.stage_duration_ms),
            "tool_calls": self.tool_calls,
            "blocked_executions": self.blocked_executions,
            "finding_severity": dict(self.finding_severity),
            "errors": dict(self.errors),
        }
