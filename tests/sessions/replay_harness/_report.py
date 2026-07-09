#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Diff report generation for replay consistency testing.

Produces a ``session_memory_summary_diff_report.json`` that documents
every discrepancy across backends with field-level precision.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

from ._comparator import DiffEntry


class ReportMetadata(BaseModel):
    """Run-level metadata for the diff report."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for this report run."""

    timestamp: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    """ISO-8601 timestamp of report generation."""

    backends: list[str] = Field(default_factory=list)
    """Backend labels that were tested (e.g. ``["in_memory", "sql"]``)."""


class CaseResult(BaseModel):
    """Per-case summary in the report."""

    case_id: str
    """Identifier of the replay case."""

    description: str = ""
    """Human-readable description of the case."""

    status: str = "pass"
    """Overall status: ``"pass"``, ``"fail"``, or ``"error"``."""

    diffs: list[DiffEntry] = Field(default_factory=list)
    """All diffs found for this case."""

    is_anomaly_case: bool = False
    """True for injected-anomaly cases whose diffs are expected."""


class ReportSummary(BaseModel):
    """Aggregate statistics for the report."""

    total: int = 0
    """Total number of cases executed."""

    passed: int = 0
    """Number of cases that passed."""

    failed: int = 0
    """Number of cases that failed."""

    error: int = 0
    """Number of cases that errored during execution."""

    false_positive_rate: float = 0.0
    """False-positive rate (fraction of passing cases that had allowed diffs)."""


class DiffReport(BaseModel):
    """Top-level diff report model."""

    metadata: ReportMetadata = Field(default_factory=ReportMetadata)
    """Run metadata."""

    results: list[CaseResult] = Field(default_factory=list)
    """Per-case results."""

    summary: ReportSummary = Field(default_factory=ReportSummary)
    """Aggregate statistics."""


def _compute_false_positive_rate(results: list[CaseResult]) -> float:
    """Compute false-positive rate for non-anomaly passing cases."""
    passing = [r for r in results if r.status == "pass" and not r.is_anomaly_case]
    if not passing:
        return 0.0
    false_positive_count = sum(1 for r in passing if r.diffs)
    return false_positive_count / len(passing)


def generate_report(
    results: list[CaseResult],
    backends: Optional[list[str]] = None,
) -> DiffReport:
    """Generate a ``DiffReport`` from a list of ``CaseResult`` items.

    Args:
        results: Per-case comparison results.
        backends: List of backend labels tested.

    Returns:
        A fully populated ``DiffReport``.
    """
    if backends is None:
        backends = []

    metadata = ReportMetadata(backends=backends)

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    error = sum(1 for r in results if r.status == "error")
    fps_rate = _compute_false_positive_rate(results)

    report_summary = ReportSummary(
        total=len(results),
        passed=passed,
        failed=failed,
        error=error,
        false_positive_rate=fps_rate,
    )

    return DiffReport(metadata=metadata, results=results, summary=report_summary)


def write_report(report: DiffReport, path: Path) -> None:
    """Serialize *report* to a JSON file at *path*.

    Args:
        report: The report to write.
        path: Destination file path.
    """
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report.model_dump_json(indent=2))


def report_to_dict(report: DiffReport) -> dict[str, Any]:
    """Convert a ``DiffReport`` to a plain dictionary.

    Useful for programmatic inspection in tests.
    """
    return json.loads(report.model_dump_json())
