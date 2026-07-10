from __future__ import annotations

from .models import CaseDelta, CaseSnapshot


def compare_case(
    baseline: CaseSnapshot,
    candidate: CaseSnapshot,
    *,
    epsilon: float,
    critical_case_ids: set[str],
) -> CaseDelta:
    if baseline.eval_id != candidate.eval_id:
        raise ValueError("baseline and candidate must use the same eval_id")
    if not baseline.passed and candidate.passed:
        transition = "NEW_PASS"
    elif baseline.passed and not candidate.passed:
        transition = "REGRESSION"
    elif candidate.aggregate_score > baseline.aggregate_score + epsilon:
        transition = "IMPROVED"
    elif candidate.aggregate_score < baseline.aggregate_score - epsilon:
        transition = "DEGRADED"
    else:
        transition = "UNCHANGED"
    metric_names = set(baseline.metric_scores) | set(candidate.metric_scores)
    critical = baseline.eval_id in critical_case_ids
    return CaseDelta(
        eval_id=baseline.eval_id,
        baseline_passed=baseline.passed,
        candidate_passed=candidate.passed,
        transition=transition,
        baseline_score=baseline.aggregate_score,
        candidate_score=candidate.aggregate_score,
        score_delta=candidate.aggregate_score - baseline.aggregate_score,
        metric_deltas={name: candidate.metric_scores.get(name, 0.0) - baseline.metric_scores.get(name, 0.0) for name in metric_names},
        critical=critical,
        hard_fail_added=(not baseline.hard_failed and candidate.hard_failed) or (critical and transition == "REGRESSION"),
    )
