# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Stage 4: Delta comparison between baseline and candidate on validation set."""

from __future__ import annotations

from .config import BaselineResult, CaseDelta, DeltaReport


def _classify_change(
    case_id: str,
    baseline_status: str,
    candidate_status: str,
    baseline_scores: dict[str, float],
    candidate_scores: dict[str, float],
    epsilon: float = 0.01,
) -> tuple[str, str]:
    """Classify the change between baseline and candidate for one case.

    Returns (change_type, description).
    """
    if baseline_status == "FAILED" and candidate_status == "PASSED":
        return "newly_passing", f"Case '{case_id}': FAILED → PASSED (newly passing)"
    elif baseline_status == "PASSED" and candidate_status == "FAILED":
        return "newly_failing", f"Case '{case_id}': PASSED → FAILED (hard regression)"
    elif baseline_status == "PASSED" and candidate_status == "PASSED":
        # Check score deltas for same-status cases
        for metric, bs in baseline_scores.items():
            cs = candidate_scores.get(metric, 0.0)
            if cs < bs - epsilon:
                return "degraded", f"Case '{case_id}': PASSED but score degraded on '{metric}' ({bs:.2f}→{cs:.2f})"
        return "unchanged", f"Case '{case_id}': PASSED → PASSED (no significant change)"
    elif baseline_status == "FAILED" and candidate_status == "FAILED":
        # Check if scores improved
        improved = False
        degraded = False
        for metric, bs in baseline_scores.items():
            cs = candidate_scores.get(metric, 0.0)
            if cs > bs + epsilon:
                improved = True
            elif cs < bs - epsilon:
                degraded = True
        if improved:
            return "improved", f"Case '{case_id}': FAILED but scores improved"
        elif degraded:
            return "degraded", f"Case '{case_id}': FAILED and scores further degraded"
        return "unchanged", f"Case '{case_id}': FAILED → FAILED (no change)"
    return "unchanged", f"Case '{case_id}': status unchanged"


def compare(
    baseline: BaselineResult,
    candidate: BaselineResult,
) -> DeltaReport:
    """Compare baseline and candidate evaluation results per-case.

    Args:
        baseline: BaselineResult for validation set before optimization.
        candidate: BaselineResult for validation set after optimization.

    Returns:
        DeltaReport with per-case delta and aggregate pass_rate change.
    """
    baseline_by_id = {c.case_id: c for c in baseline.per_case}
    candidate_by_id = {c.case_id: c for c in candidate.per_case}
    all_ids = sorted(set(baseline_by_id) | set(candidate_by_id))

    per_case: list[CaseDelta] = []

    for cid in all_ids:
        bc = baseline_by_id.get(cid)
        cc = candidate_by_id.get(cid)

        b_status = bc.overall_status if bc else "NOT_EVALUATED"
        c_status = cc.overall_status if cc else "NOT_EVALUATED"

        b_scores = {m.metric_name: m.score for m in (bc.metrics.values() if bc else [])}
        c_scores = {m.metric_name: m.score for m in (cc.metrics.values() if cc else [])}

        delta_scores = {}
        all_metrics = set(b_scores) | set(c_scores)
        for m in all_metrics:
            delta_scores[m] = c_scores.get(m, 0.0) - b_scores.get(m, 0.0)

        change_type, _ = _classify_change(cid, b_status, c_status, b_scores, c_scores)

        per_case.append(CaseDelta(
            case_id=cid,
            baseline_status=b_status,
            candidate_status=c_status,
            change_type=change_type,
            baseline_scores=b_scores,
            candidate_scores=c_scores,
            delta_scores=delta_scores,
        ))

    delta = candidate.pass_rate - baseline.pass_rate

    return DeltaReport(
        baseline_pass_rate=baseline.pass_rate,
        candidate_pass_rate=candidate.pass_rate,
        delta=delta,
        per_case=per_case,
    )
