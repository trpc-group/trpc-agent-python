# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Replay report models and writers."""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diff import DiffEntry


@dataclass
class Metrics:
    """Automatically computed replay acceptance metrics."""

    normal_case_count: int = 0
    normal_case_pass_count: int = 0
    normal_cases_with_unexpected_diff: int = 0
    unexpected_diff_count: int = 0
    allowed_diff_count: int = 0
    false_positive_rate: float = 0.0
    mutation_total: int = 0
    mutation_detected: int = 0
    survived_mutations: list[str] | None = None
    mutation_detection_rate: float = 0.0
    summary_loss_detected: int = 0
    summary_overwrite_detected: int = 0
    summary_owner_error_detected: int = 0
    summary_loss_detection_rate: float = 0.0
    summary_overwrite_detection_rate: float = 0.0
    summary_owner_error_detection_rate: float = 0.0
    runtime_fault_total: int = 0
    runtime_fault_detected: int = 0
    runtime_fault_detection_rate: float = 0.0
    lightweight_duration_seconds: float = 0.0


@dataclass
class ReplayReport:
    """Structured JSON report for replay consistency runs."""

    case_id: str
    backend_pair: tuple[str, str]
    metrics: Metrics
    diffs: list[dict[str, Any]]
    schema_version: str = "1.0"
    harness_version: str = "1"
    mode: str = "lightweight"
    capabilities: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["unexpected_diffs"] = [diff for diff in self.diffs if not diff.get("allowed")]
        data["allowed_diffs"] = [diff for diff in self.diffs if diff.get("allowed")]
        data["unused_allowed_diff_rules"] = data.get("unused_allowed_diff_rules", [])
        return data


def build_metrics(
    *,
    normal_case_count: int,
    normal_case_pass_count: int,
    diffs: list[DiffEntry],
    mutation_total: int = 0,
    mutation_detected: int = 0,
    summary_loss_detected: int = 0,
    summary_overwrite_detected: int = 0,
    summary_owner_error_detected: int = 0,
    summary_loss_total: int = 0,
    summary_overwrite_total: int = 0,
    summary_owner_error_total: int = 0,
    runtime_fault_total: int = 0,
    runtime_fault_detected: int = 0,
    lightweight_duration_seconds: float = 0.0,
    survived_mutations: list[str] | None = None,
) -> Metrics:
    unexpected = [diff for diff in diffs if not diff.allowed]
    allowed = [diff for diff in diffs if diff.allowed]
    false_positive_rate = 0.0
    if normal_case_count:
        false_positive_rate = (normal_case_count - normal_case_pass_count) / normal_case_count
    mutation_detection_rate = 0.0
    if mutation_total:
        mutation_detection_rate = mutation_detected / mutation_total
    summary_loss_detection_rate = _rate(summary_loss_detected, summary_loss_total)
    summary_overwrite_detection_rate = _rate(summary_overwrite_detected, summary_overwrite_total)
    summary_owner_error_detection_rate = _rate(summary_owner_error_detected, summary_owner_error_total)
    runtime_fault_detection_rate = _rate(runtime_fault_detected, runtime_fault_total)
    return Metrics(
        normal_case_count=normal_case_count,
        normal_case_pass_count=normal_case_pass_count,
        normal_cases_with_unexpected_diff=normal_case_count - normal_case_pass_count,
        unexpected_diff_count=len(unexpected),
        allowed_diff_count=len(allowed),
        false_positive_rate=false_positive_rate,
        mutation_total=mutation_total,
        mutation_detected=mutation_detected,
        survived_mutations=survived_mutations or [],
        mutation_detection_rate=mutation_detection_rate,
        summary_loss_detected=summary_loss_detected,
        summary_overwrite_detected=summary_overwrite_detected,
        summary_owner_error_detected=summary_owner_error_detected,
        summary_loss_detection_rate=summary_loss_detection_rate,
        summary_overwrite_detection_rate=summary_overwrite_detection_rate,
        summary_owner_error_detection_rate=summary_owner_error_detection_rate,
        runtime_fault_total=runtime_fault_total,
        runtime_fault_detected=runtime_fault_detected,
        runtime_fault_detection_rate=runtime_fault_detection_rate,
        lightweight_duration_seconds=lightweight_duration_seconds,
    )


def write_json_report(path: Path, report: ReplayReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def write_markdown_report(path: Path, report: ReplayReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Replay Report: {report.case_id}",
        "",
        f"- Backend pair: {report.backend_pair[0]} vs {report.backend_pair[1]}",
        f"- Unexpected diffs: {report.metrics.unexpected_diff_count}",
        f"- Allowed diffs: {report.metrics.allowed_diff_count}",
        f"- Mutation detection rate: {report.metrics.mutation_detection_rate:.2%}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rate(detected: int, total: int) -> float:
    if total == 0:
        return 0.0
    return detected / total
