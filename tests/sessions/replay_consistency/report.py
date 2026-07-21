# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Structured diff report generation for replay consistency tests.

Produces a JSON report (schema_version=3) that records:
- Backend availability statuses
- Per-case diff counts (allowed / unallowed / unexpected)
- Detailed DiffEntry list with precise field-level location
- False-positive summary for normal replay
- Mutation detection summary for injection tests

The report is written to the repository root as
session_memory_summary_diff_report.json (runtime artifact).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .comparator import DiffEntry
from .harness import BackendStatus
from .harness import Report
from .summary_checks import SummaryIssue


def write_report(
    diffs: list[DiffEntry],
    backend_statuses: list[BackendStatus],
    backend_pairs: list[str],
    case_results: list[dict[str, Any]],
    summary_issues: list[SummaryIssue] | None = None,
    mutation_meta: list[dict[str, Any]] | None = None,
    output_path: Path | str | None = None,
    *,
    report_kind: str = "normal_replay",
) -> Report:
    """Build and write a structured replay consistency report.

    Args:
        diffs: All DiffEntry objects from the comparison.
        backend_statuses: Per-backend status (ok/skipped/error).
        backend_pairs: List of compared backend pair names.
        case_results: Per-case metadata dicts with name, elapsed_ms,
            allowed_diff_count, unallowed_diff_count, unexpected_diff_count.
        summary_issues: Optional list of detected summary faults.
        mutation_meta: Optional list of mutation metadata for injection tests.
        output_path: Where to write the JSON report.  If None, no file is written.
        report_kind: "normal_replay" or "mutation_replay".

    Returns:
        The populated Report object.
    """
    allowed_diffs = [d for d in diffs if d.allowed]
    unallowed_diffs = [d for d in diffs if not d.allowed]

    report = Report(
        schema_version=3,
        report_kind=report_kind,
        generated_by="tests/sessions/test_replay_consistency.py",
        generated_at="deterministic",
        backend_statuses=backend_statuses,
        backend_pairs=backend_pairs,
        case_count=len(case_results),
        cases=sorted(case_results, key=lambda c: c.get("name", "")),
        diffs=diffs,
        false_positive_summary={
            "normal_case_count": len(case_results) if report_kind == "normal_replay" else 0,
            "unexpected_diff_count": sum(
                c.get("unexpected_diff_count", 0) for c in case_results
            ),
        },
        mutation_summary={
            "mutation_count": len(mutation_meta) if mutation_meta else 0,
            "detected_count": sum(
                1 for m in (mutation_meta or []) if m.get("detected", False)
            ),
            "undetected_mutations": [
                m.get("mutation", "") for m in (mutation_meta or [])
                if not m.get("detected", False)
            ],
        },
        allowed_diff_count=len(allowed_diffs),
        unallowed_diff_count=len(unallowed_diffs),
        unexpected_diff_count=sum(
            c.get("unexpected_diff_count", 0) for c in case_results
        ),
    )

    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        report_json = _report_to_json(report, summary_issues or [])
        with open(output, "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2, ensure_ascii=False, default=str)
        # Also add trailing newline
        with open(output, "a", encoding="utf-8") as f:
            f.write("\n")

    return report


def _report_to_json(report: Report, summary_issues: list[SummaryIssue]) -> dict[str, Any]:
    """Convert a Report to a JSON-serializable dict.

    Args:
        report: The Report object to serialize.
        summary_issues: Summary issues to inline in the report.

    Returns:
        A JSON-serializable dict.
    """
    return {
        "schema_version": report.schema_version,
        "report_kind": report.report_kind,
        "generated_by": report.generated_by,
        "generated_at": report.generated_at,
        "backend_statuses": [
            bs.model_dump() for bs in report.backend_statuses
        ],
        "backend_pairs": report.backend_pairs,
        "case_count": report.case_count,
        "cases": report.cases,
        "diffs": [_diff_to_dict(d) for d in report.diffs],
        "summary_issues": [si.model_dump() for si in summary_issues],
        "false_positive_summary": report.false_positive_summary,
        "mutation_summary": report.mutation_summary,
        "allowed_diff_count": report.allowed_diff_count,
        "unallowed_diff_count": report.unallowed_diff_count,
        "unexpected_diff_count": report.unexpected_diff_count,
    }


def _diff_to_dict(diff: DiffEntry) -> dict[str, Any]:
    """Convert a single DiffEntry to a JSON-safe dict."""
    return {
        "case_name": diff.case_name,
        "left_backend": diff.left_backend,
        "right_backend": diff.right_backend,
        "session_id": diff.session_id,
        "event_index": diff.event_index,
        "memory_index": diff.memory_index,
        "summary_id": diff.summary_id,
        "section": diff.section,
        "path": diff.path,
        "left": _safe_value(diff.left),
        "right": _safe_value(diff.right),
        "allowed": diff.allowed,
        "reason": diff.reason,
    }


def _safe_value(value: Any) -> Any:
    """Convert a value to a JSON-safe representation."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)
