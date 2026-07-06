# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Per-review monitoring metrics (issue requirement 9).

Numbers live in a plain dataclass persisted to ``cr_report.metrics`` so they
are DB-queryable; pipeline phases are additionally wrapped in
``trpc_agent_sdk.telemetry.tracer`` spans for OTel-based observability.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from typing import Dict


@dataclass
class ReviewMetrics:
    """Everything the issue asks to monitor, in one queryable object."""

    total_duration_ms: float = 0.0
    sandbox_duration_ms: float = 0.0
    sandbox_run_count: int = 0
    tool_call_count: int = 0
    llm_call_count: int = 0
    filter_block_count: int = 0
    filter_decisions: Dict[str, int] = field(
        default_factory=lambda: {"allow": 0, "deny": 0, "needs_human_review": 0})
    finding_count: int = 0
    needs_human_review_count: int = 0
    deduplicated_count: int = 0
    redaction_count: int = 0
    severity_distribution: Dict[str, int] = field(default_factory=dict)
    error_types: Dict[str, int] = field(default_factory=dict)

    def record_filter_decision(self, action: str) -> None:
        self.filter_decisions[action] = self.filter_decisions.get(action, 0) + 1
        if action in ("deny", "needs_human_review"):
            self.filter_block_count += 1

    def record_error(self, error_type: str) -> None:
        if error_type:
            self.error_types[error_type] = self.error_types.get(error_type, 0) + 1

    def to_dict(self) -> dict:
        return {
            "total_duration_ms": round(self.total_duration_ms, 2),
            "sandbox_duration_ms": round(self.sandbox_duration_ms, 2),
            "sandbox_run_count": self.sandbox_run_count,
            "tool_call_count": self.tool_call_count,
            "llm_call_count": self.llm_call_count,
            "filter_block_count": self.filter_block_count,
            "filter_decisions": dict(self.filter_decisions),
            "finding_count": self.finding_count,
            "needs_human_review_count": self.needs_human_review_count,
            "deduplicated_count": self.deduplicated_count,
            "redaction_count": self.redaction_count,
            "severity_distribution": dict(self.severity_distribution),
            "error_types": dict(self.error_types),
        }


@contextmanager
def phase_timer():
    """``with phase_timer() as elapsed: ...; ms = elapsed()`` helper."""
    start = time.perf_counter()
    yield lambda: (time.perf_counter() - start) * 1000.0
