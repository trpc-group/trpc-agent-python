# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""schema_version=3 差异报告组装。

每条 diff 内联 session_id / event_index / summary_id / field_path / 双后端值;
``false_positive_rate`` 仅按正常 case 计算(注入 case 不计入)。
单后端(轻量只剩 InMemory)时用 ``not_applicable`` 诚实标记,而非假 ``match``。
"""

from __future__ import annotations

import json
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field

from .comparator import DiffEntry
from .summary_checks import SummaryIssue

CaseStatus = Literal["match", "mismatch", "not_applicable", "skipped"]


class BackendStatus(BaseModel):
    name: str
    status: CaseStatus
    reason: str | None = None


class Comparison(BaseModel):
    candidate_backend: str
    status: CaseStatus
    diffs: list[DiffEntry] = Field(default_factory=list)
    summary_issues: list[SummaryIssue] = Field(default_factory=list)


class CaseResult(BaseModel):
    case_id: str
    session_id: str
    comparisons: list[Comparison] = Field(default_factory=list)


def _roll_up_status(comparisons: list[Comparison]) -> CaseStatus:
    """一个 case 跨所有候选后端的汇总状态。"""
    if not comparisons:
        return "not_applicable"
    statuses = {c.status for c in comparisons}
    if "mismatch" in statuses:
        return "mismatch"
    if statuses == {"skipped"}:
        return "skipped"
    if statuses <= {"not_applicable"}:
        return "not_applicable"
    return "match"


def _compared_backends(statuses: list[BackendStatus], case_results: list[CaseResult]) -> list[str]:
    compared = [b.name for b in statuses if b.status != "skipped"]
    if compared:
        return compared
    seen: list[str] = []
    for cr in case_results:
        for c in cr.comparisons:
            if c.candidate_backend not in seen:
                seen.append(c.candidate_backend)
    return seen


def build_diff_report(
    reference_backend: str,
    case_results: list[CaseResult],
    backend_statuses: list[BackendStatus] | None = None,
) -> dict[str, Any]:
    """组装差异报告 dict(可 json.dump)。"""
    statuses = backend_statuses or []
    totals = {
        "cases": len(case_results),
        "matched": 0,
        "mismatched": 0,
        "not_applicable": 0,
        "skipped": 0,
    }
    cases_out: list[dict[str, Any]] = []
    normal_mismatch = 0
    for cr in case_results:
        st = _roll_up_status(cr.comparisons)
        if st == "match":
            totals["matched"] += 1
        elif st == "mismatch":
            totals["mismatched"] += 1
            normal_mismatch += 1
        elif st == "not_applicable":
            totals["not_applicable"] += 1
        elif st == "skipped":
            totals["skipped"] += 1
        cases_out.append({
            "case_id": cr.case_id,
            "session_id": cr.session_id,
            "status": st,
            "comparisons": [c.model_dump() for c in cr.comparisons],
        })

    fpr = (normal_mismatch / len(case_results)) if case_results else 0.0
    return {
        "schema_version": 3,
        "reference_backend": reference_backend,
        "compared_backends": _compared_backends(statuses, case_results),
        "backend_statuses": [b.model_dump() for b in statuses],
        "totals": totals,
        "false_positive_rate": fpr,
        "cases": cases_out,
    }


def write_report(report: dict[str, Any], path: str) -> None:
    """把报告写入 JSON 文件(仓库根 session_memory_summary_diff_report.json)。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
