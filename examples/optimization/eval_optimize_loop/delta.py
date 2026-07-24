from __future__ import annotations

from .models import PerCaseDelta, SplitDelta, SplitResult


def _per_case_delta(baseline: SplitResult, candidate: SplitResult) -> PerCaseDelta:
    newly_passing: list[str] = []
    newly_failing: list[str] = []
    unchanged: list[str] = []
    score_deltas: dict[str, dict[str, float]] = {}

    all_case_ids = set(baseline.per_case.keys()) | set(candidate.per_case.keys())

    for case_id in all_case_ids:
        base_case = baseline.per_case.get(case_id)
        cand_case = candidate.per_case.get(case_id)

        base_passed = base_case.passed if base_case else False
        cand_passed = cand_case.passed if cand_case else False

        if not base_passed and cand_passed:
            newly_passing.append(case_id)
        elif base_passed and not cand_passed:
            newly_failing.append(case_id)
        else:
            unchanged.append(case_id)

        case_delta: dict[str, float] = {}
        base_scores = base_case.metric_scores if base_case else {}
        cand_scores = cand_case.metric_scores if cand_case else {}

        all_metrics = set(base_scores.keys()) | set(cand_scores.keys())
        for metric_name in all_metrics:
            base_val = base_scores.get(metric_name, 0.0)
            cand_val = cand_scores.get(metric_name, 0.0)
            case_delta[metric_name] = cand_val - base_val

        score_deltas[case_id] = case_delta

    return PerCaseDelta(
        newly_passing=newly_passing,
        newly_failing=newly_failing,
        score_deltas=score_deltas,
        unchanged=unchanged,
    )


def compute_delta(baseline: dict[str, SplitResult], candidate: dict[str, SplitResult]) -> SplitDelta:
    train_delta = _per_case_delta(baseline["train"], candidate["train"])
    val_delta = _per_case_delta(baseline["val"], candidate["val"])

    return SplitDelta(
        train=train_delta,
        val=val_delta,
        train_pass_rate_delta=candidate["train"].pass_rate - baseline["train"].pass_rate,
        val_pass_rate_delta=candidate["val"].pass_rate - baseline["val"].pass_rate,
    )
