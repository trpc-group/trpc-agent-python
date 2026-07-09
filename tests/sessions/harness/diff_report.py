# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Diff report generation for cross-backend replay consistency tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from .comparator import Comparator
from .snapshot import BackendSnapshot


@dataclass
class DiffReport:
    """Aggregated diff report for a single replay case across backends.

    Attributes:
        case_name: Name of the replay case.
        backend_a: Name of the baseline backend.
        backend_b: Name of the target backend.
        diffs: All field-level diffs found.
        summary_issues: Critical summary issues detected (loss, ownership, overwrite).
        passed: Whether the case passed (no unallowed diffs).
    """

    case_name: str
    backend_a: str
    backend_b: str
    diffs: list[dict[str, Any]] = field(default_factory=list)
    summary_issues: list[dict[str, Any]] = field(default_factory=list)
    allowed_diff_count: int = 0
    unallowed_diff_count: int = 0

    @property
    def passed(self) -> bool:
        return self.unallowed_diff_count == 0 and len(self.summary_issues) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "backend_a": self.backend_a,
            "backend_b": self.backend_b,
            "passed": self.passed,
            "allowed_diff_count": self.allowed_diff_count,
            "unallowed_diff_count": self.unallowed_diff_count,
            "summary_issues": self.summary_issues,
            "diffs": self.diffs,
        }


class DiffReportGenerator:
    """Generates diff reports by comparing backend snapshots."""

    def __init__(self):
        self._comparator = Comparator()

    def generate(
        self,
        case_name: str,
        baseline: BackendSnapshot,
        target: BackendSnapshot,
    ) -> DiffReport:
        diffs = self._comparator.compare(baseline, target)
        summary_issues = self._comparator.check_summary_issues(baseline, target)
        allowed = [d for d in diffs if d.get("allowed")]
        unallowed = [d for d in diffs if not d.get("allowed")]
        return DiffReport(
            case_name=case_name,
            backend_a=baseline.backend_name,
            backend_b=target.backend_name,
            diffs=diffs,
            summary_issues=summary_issues,
            allowed_diff_count=len(allowed),
            unallowed_diff_count=len(unallowed),
        )

    def generate_all(
        self,
        case_name: str,
        snapshots: dict[str, BackendSnapshot],
        baseline_name: str = "inmemory",
    ) -> list[DiffReport]:
        baseline = snapshots.get(baseline_name)
        if baseline is None:
            raise ValueError(f"Baseline backend '{baseline_name}' not found in snapshots")
        reports: list[DiffReport] = []
        for name, snapshot in snapshots.items():
            if name == baseline_name:
                continue
            reports.append(self.generate(case_name, baseline, snapshot))
        return reports

    def save_aggregated_report(
        self,
        all_reports: list[DiffReport],
        output_path: str | Path,
    ) -> None:
        output = {
            "generated_at": "",
            "total_cases": len(set(r.case_name for r in all_reports)),
            "total_reports": len(all_reports),
            "passed_count": sum(1 for r in all_reports if r.passed),
            "failed_count": sum(1 for r in all_reports if not r.passed),
            "reports": [r.to_dict() for r in all_reports],
        }
        from datetime import datetime
        output["generated_at"] = datetime.now().isoformat()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)
