"""Telemetry summary helpers for the deterministic code review example."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from .filtering import FilterDecision
from .findings import Finding
from .sandbox import SandboxRun


def _counts(items: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(items).items()))


def build_telemetry_summary(
    *,
    files_scanned: Sequence[str],
    findings: Sequence[Finding],
    sandbox_run: SandboxRun,
    filter_decision: FilterDecision,
    duration_ms: int,
) -> dict[str, object]:
    """Build a small telemetry summary for reports and storage."""

    return {
        "files_scanned": list(files_scanned),
        "total_findings": len(findings),
        "severity_counts": _counts([finding.severity for finding in findings]),
        "category_counts": _counts([finding.category for finding in findings]),
        "sandbox_status": sandbox_run.status,
        "filter_decision": filter_decision.decision,
        "duration_ms": max(0, int(duration_ms)),
    }
