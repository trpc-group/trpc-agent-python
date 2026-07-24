"""Report generation — JSON and Markdown optimization reports with audit trail."""

import json
from datetime import datetime, timezone
from typing import Any

from .attribution import AttributionReport
from .baseline import BaselineResult
from .gate import GateResult
from .validate import ValidationResult


def generate_json_report(
    task_id: str,
    baseline_train: BaselineResult,
    baseline_val: BaselineResult,
    attribution: AttributionReport,
    gate: GateResult,
    validation: ValidationResult | None = None,
    optimization_result: dict | None = None,
    audit: dict | None = None,
) -> str:
    """Generate a JSON-format optimization report.

    Args:
        task_id: Unique task identifier.
        baseline_train: Training set baseline results.
        baseline_val: Validation set baseline results.
        attribution: Failure attribution report.
        gate: Gate decision.
        validation: Validation comparison (optional).
        optimization_result: Optimizer details.
        audit: Audit trail (seeds, timing, cost).

    Returns:
        JSON string.
    """
    report = {
        "task_id": task_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline": {
            "train": _baseline_to_dict(baseline_train),
            "validation": _baseline_to_dict(baseline_val),
        },
        "attribution": {
            "total_failures": attribution.total_failures,
            "by_category": attribution.by_category,
            "entries": [
                {
                    "case_id": e.case_id,
                    "category": e.category.value,
                    "confidence": e.confidence,
                    "detail": e.detail,
                }
                for e in attribution.entries
            ],
        },
        "gate": {
            "decision": gate.decision.value,
            "reason": gate.reason,
            "checks": gate.details.get("checks", []),
        },
        "validation_delta": _validation_to_dict(validation) if validation else {},
        "optimizer": optimization_result or {},
        "audit": audit or {},
    }

    return json.dumps(report, indent=2, ensure_ascii=False)


def generate_md_report(
    task_id: str,
    baseline_train: BaselineResult,
    baseline_val: BaselineResult,
    attribution: AttributionReport,
    gate: GateResult,
    validation: ValidationResult | None = None,
    audit: dict | None = None,
) -> str:
    """Generate a human-readable Markdown optimization report."""
    audit = audit or {}
    improvement = audit.get("improvement", 0.0)
    cost = audit.get("optimization_cost", 0.0)
    duration = audit.get("duration_seconds", 0)

    lines = [
        f"# Optimization Report",
        f"",
        f"**Task ID**: `{task_id}`",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Baseline | Candidate | Delta |",
        f"|--------|----------|-----------|-------|",
        f"| Train Pass Rate | {baseline_train.pass_rate:.1%} | — | — |",
        f"| Val Pass Rate | {baseline_val.pass_rate:.1%} | — | — |",
        f"",
        f"## Gate Decision",
        f"",
        f"**Decision**: {'✅ ACCEPT' if gate.decision.value == 'accept' else '❌ REJECT' if gate.decision.value == 'reject' else '⚠️ NEEDS REVIEW'}",
        f"",
        f"**Reason**: {gate.reason}",
        f"",
    ]

    # Gate checks
    if gate.details.get("checks"):
        lines.append(f"### Gate Checks")
        lines.append(f"")
        lines.append(f"| Check | Result | Detail |")
        lines.append(f"|-------|--------|--------|")
        for check in gate.details["checks"]:
            icon = "✅" if check["passed"] else "❌"
            lines.append(f"| {check['check']} | {icon} | {check['detail']} |")
        lines.append(f"")

    # Failure attribution
    lines.append(f"## Failure Attribution")
    lines.append(f"")
    if attribution.total_failures == 0:
        lines.append(f"No failures to attribute. ✅")
    else:
        lines.append(f"Total failures: **{attribution.total_failures}**")
        lines.append(f"")
        lines.append(f"| Category | Count |")
        lines.append(f"|----------|-------|")
        for cat, count in sorted(attribution.by_category.items(),
                                  key=lambda x: x[1], reverse=True):
            lines.append(f"| {cat} | {count} |")
    lines.append(f"")

    # Validation delta
    if validation:
        lines.append(f"## Validation Set Comparison")
        lines.append(f"")
        lines.append(f"| Change | Count |")
        lines.append(f"|--------|-------|")
        lines.append(f"| New Passes | {validation.new_passes} |")
        lines.append(f"| New Failures | {validation.new_failures} |")
        lines.append(f"| Unchanged | {validation.unchanged} |")
        lines.append(f"")
        if validation.is_overfitting:
            lines.append(f"⚠️ **Overfitting detected**: Validation set degraded while training set improved.")
            lines.append(f"")

    # Audit
    if audit:
        lines.append(f"## Audit Trail")
        lines.append(f"")
        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| Seed | {audit.get('seed', 'N/A')} |")
        lines.append(f"| Duration | {duration:.1f}s |")
        lines.append(f"| Optimization Cost | ${cost:.2f} |")
        lines.append(f"| Mode | {audit.get('mode', 'fake')} |")
        if audit.get("reproduce_command"):
            lines.append(f"| Reproduce | `{audit['reproduce_command']}` |")
        lines.append(f"")

    # Recommendations
    lines.append(f"## Recommendations")
    lines.append(f"")
    if gate.decision.value == "accept":
        lines.append(f"- ✅ Accept the optimized prompt — improvement verified.")
    elif gate.decision.value == "reject":
        lines.append(f"- ❌ Reject the candidate — see gate reason above.")
        if attribution.total_failures > 0:
            top_cat = max(attribution.by_category.items(), key=lambda x: x[1])
            lines.append(f"- Focus on fixing `{top_cat[0]}` issues ({top_cat[1]} failures).")
    else:
        lines.append(f"- ⚠️ Manual review recommended before accepting.")
    lines.append(f"")

    return "\n".join(lines)


def _baseline_to_dict(bl: BaselineResult) -> dict:
    """Convert BaselineResult to serializable dict."""
    return {
        "evalset_id": bl.evalset_id,
        "pass_rate": bl.pass_rate,
        "total_cases": bl.total_cases,
        "passed_cases": bl.passed_cases,
        "failed_cases": bl.failed_cases,
        "failed_case_ids": bl.failed_case_ids,
        "metric_breakdown": bl.metric_breakdown,
    }


def _validation_to_dict(v: ValidationResult) -> dict:
    """Convert ValidationResult to serializable dict."""
    return {
        "new_passes": v.new_passes,
        "new_failures": v.new_failures,
        "unchanged": v.unchanged,
        "is_overfitting": v.is_overfitting,
        "deltas": [
            {
                "eval_id": d.eval_id,
                "baseline_passed": d.baseline_passed,
                "candidate_passed": d.candidate_passed,
                "change": d.change,
            }
            for d in v.deltas
        ],
    }
