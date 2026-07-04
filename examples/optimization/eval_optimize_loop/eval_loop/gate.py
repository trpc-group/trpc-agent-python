"""Configurable acceptance gate for candidate prompts."""

from __future__ import annotations

from typing import Any

from .schemas import CaseDelta
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
    ) -> GateDecision:
        train_delta = round(candidate_train.score - baseline_train.score, 6)
        val_delta = round(candidate_validation.score - baseline_validation.score, 6)
        candidate_cost = round(candidate_train.cost + candidate_validation.cost, 6)
        reasons: list[str] = []

        if train_delta > 0 and val_delta < 0:
            reasons.append(
                "reject: train score improved but validation score regressed "
                f"({train_delta:+.3f} train, {val_delta:+.3f} validation)"
            )

        min_val_improvement = float(self.config["min_val_score_improvement"])
        if val_delta < min_val_improvement:
            reasons.append(
                "reject: validation improvement "
                f"{val_delta:+.3f} is below required {min_val_improvement:+.3f}"
            )

        baseline_validation_by_id = baseline_validation.by_case_id()
        candidate_validation_by_id = candidate_validation.by_case_id()
        new_hard_failures = [
            case_id
            for case_id, candidate_case in sorted(candidate_validation_by_id.items())
            if candidate_case.hard_failed and baseline_validation_by_id.get(case_id)
            and baseline_validation_by_id[case_id].passed
        ]
        if new_hard_failures and not bool(self.config["allow_new_hard_fail"]):
            reasons.append(f"reject: new hard failures appeared: {new_hard_failures}")

        protected_ids = set(str(item) for item in self.config["protected_case_ids"])
        protected_regressions = [
            delta.case_id
            for delta in deltas
            if delta.split == "validation" and delta.case_id in protected_ids and delta.delta < 0
        ]
        if protected_regressions:
            reasons.append(f"reject: protected cases regressed: {protected_regressions}")

        max_drop = float(self.config["max_score_drop_per_case"])
        excessive_drops = [
            delta.case_id
            for delta in deltas
            if delta.split == "validation" and delta.delta < -max_drop
        ]
        if excessive_drops:
            reasons.append(f"reject: per-case validation score drops exceed {max_drop:.3f}: {excessive_drops}")

        max_total_cost = float(self.config["max_total_cost"])
        if candidate_cost > max_total_cost:
            reasons.append(f"reject: candidate cost {candidate_cost:.3f} exceeds budget {max_total_cost:.3f}")

        accepted = not any(reason.startswith("reject:") for reason in reasons)
        if accepted:
            reasons.append(
                "accept: validation score improved "
                f"{val_delta:+.3f} with no protected regression or new hard failure"
            )

        return GateDecision(
            candidate_id=candidate_id,
            accepted=accepted,
            reasons=reasons,
            train_score_delta=train_delta,
            validation_score_delta=val_delta,
            new_hard_failures=new_hard_failures,
            protected_regressions=protected_regressions,
            cost=candidate_cost,
        )
