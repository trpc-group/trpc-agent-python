"""Configurable acceptance gate for candidate prompts."""

from __future__ import annotations

from typing import Any

from .schemas import CaseDelta
from .schemas import CostSummary
from .schemas import EvalResult
from .schemas import GateDecision

DEFAULT_GATE_CONFIG = {
    "min_val_score_improvement": 0.01,
    "allow_new_hard_fail": False,
    "protected_case_ids": [],
    "max_score_drop_per_case": 0.0,
    "max_total_cost": 1.0,
}


class AcceptanceGate:
    """Apply deterministic safety and quality constraints to candidates."""

    def __init__(self, config: dict[str, Any]) -> None:
        merged = dict(DEFAULT_GATE_CONFIG)
        merged.update(config or {})
        self.config = merged

    def decide(
        self,
        *,
        candidate_id: str,
        baseline_train: EvalResult,
        baseline_validation: EvalResult,
        candidate_train: EvalResult,
        candidate_validation: EvalResult,
        deltas: list[CaseDelta],
        cost_summary: CostSummary,
        cumulative_cost: float = 0.0,
    ) -> GateDecision:
        train_delta = round(candidate_train.score - baseline_train.score, 6)
        val_delta = round(candidate_validation.score - baseline_validation.score, 6)
        candidate_cost = round(candidate_train.cost + candidate_validation.cost, 6)
        reasons: list[str] = []

        train_comparable = _append_comparability_reasons(
            reasons,
            split="train",
            baseline=baseline_train,
            candidate=candidate_train,
        )
        validation_comparable = _append_comparability_reasons(
            reasons,
            split="validation",
            baseline=baseline_validation,
            candidate=candidate_validation,
        )

        overfit_detected = train_delta > 0 and val_delta <= 0
        if overfit_detected:
            reasons.append("reject: overfit detected because train score improved but "
                           "validation score regressed or did not improve "
                           f"({train_delta:+.3f} train, {val_delta:+.3f} validation)")

        min_val_improvement = float(self.config["min_val_score_improvement"])
        if val_delta < min_val_improvement:
            reasons.append("reject: validation improvement "
                           f"{val_delta:+.3f} is below required {min_val_improvement:+.3f}")

        validation_new_failures: list[str] = []
        if validation_comparable:
            baseline_validation_by_id = baseline_validation.by_case_id()
            candidate_validation_by_id = candidate_validation.by_case_id()
            validation_new_failures = [
                case_id for case_id, candidate_case in sorted(candidate_validation_by_id.items())
                if not candidate_case.passed and baseline_validation_by_id[case_id].passed
            ]
        if validation_new_failures:
            reasons.append(f"reject: new validation failures appeared: {validation_new_failures}")

        new_hard_failures: list[str] = []
        for baseline_result, candidate_result, comparable in (
            (baseline_train, candidate_train, train_comparable),
            (baseline_validation, candidate_validation, validation_comparable),
        ):
            if not comparable:
                continue
            baseline_by_id = baseline_result.by_case_id()
            new_hard_failures.extend(case_id
                                     for case_id, candidate_case in sorted(candidate_result.by_case_id().items())
                                     if candidate_case.hard_failed and not baseline_by_id[case_id].hard_failed)
        new_hard_failures = sorted(set(new_hard_failures))
        if new_hard_failures and not bool(self.config["allow_new_hard_fail"]):
            reasons.append(f"reject: new hard failures appeared: {new_hard_failures}")

        protected_ids = set(str(item) for item in self.config["protected_case_ids"])
        protected_regressions = [
            delta.case_id for delta in deltas
            if delta.split == "validation" and delta.case_id in protected_ids and delta.delta < 0
        ]
        if protected_regressions:
            reasons.append(f"reject: protected cases regressed: {protected_regressions}")

        max_drop = float(self.config["max_score_drop_per_case"])
        excessive_drops = [delta.case_id for delta in deltas if delta.split == "validation" and delta.delta < -max_drop]
        if excessive_drops:
            reasons.append(f"reject: per-case validation score drops exceed {max_drop:.3f}: {excessive_drops}")

        total_run_cost = round(cumulative_cost + candidate_cost, 6)
        configured_max_total_cost = self.config["max_total_cost"]
        if configured_max_total_cost is not None:
            max_total_cost = float(configured_max_total_cost)
            if not cost_summary.complete:
                reasons.append("reject: cost_unavailable for configured max_total_cost")
            elif total_run_cost > max_total_cost:
                reasons.append(f"reject: total run cost {total_run_cost:.3f} exceeds budget {max_total_cost:.3f}")

        accepted = not any(reason.startswith("reject:") for reason in reasons)
        if accepted:
            reasons.append("accept: validation score improved "
                           f"{val_delta:+.3f} with no protected regression or new hard failure")

        return GateDecision(
            candidate_id=candidate_id,
            accepted=accepted,
            reasons=reasons,
            train_score_delta=train_delta,
            validation_score_delta=val_delta,
            new_hard_failures=new_hard_failures,
            protected_regressions=protected_regressions,
            validation_new_failures=validation_new_failures,
            excessive_score_drops=excessive_drops,
            overfit_detected=overfit_detected,
            candidate_cost=candidate_cost,
            cumulative_cost=round(cumulative_cost, 6),
            total_run_cost=total_run_cost,
            cost=candidate_cost,
            gate_status="applied",
            gate_not_applied_reason=None,
            not_applied_checks=[],
        )


def _append_comparability_reasons(
    reasons: list[str],
    *,
    split: str,
    baseline: EvalResult,
    candidate: EvalResult,
) -> bool:
    """Reject malformed result pairs before any dict conversion can hide evidence."""

    comparable = True
    baseline_ids = [case.case_id for case in baseline.cases]
    candidate_ids = [case.case_id for case in candidate.cases]
    if not baseline_ids:
        reasons.append(f"reject: baseline {split} has empty case results")
        comparable = False
    if not candidate_ids:
        reasons.append(f"reject: candidate {split} has empty case results")
        comparable = False

    baseline_duplicates = _duplicates(baseline_ids)
    candidate_duplicates = _duplicates(candidate_ids)
    if baseline_duplicates:
        reasons.append(f"reject: baseline {split} has duplicate case IDs: {baseline_duplicates}")
        comparable = False
    if candidate_duplicates:
        reasons.append(f"reject: candidate {split} has duplicate case IDs: {candidate_duplicates}")
        comparable = False

    baseline_set = set(baseline_ids)
    candidate_set = set(candidate_ids)
    if baseline_set != candidate_set:
        reasons.append(f"reject: {split} case ID set mismatch; "
                       f"missing={sorted(baseline_set - candidate_set)}, "
                       f"extra={sorted(candidate_set - baseline_set)}")
        comparable = False
    return comparable


def _duplicates(case_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for case_id in case_ids:
        if case_id in seen:
            duplicates.add(case_id)
        seen.add(case_id)
    return sorted(duplicates)
