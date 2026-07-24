from __future__ import annotations

from .models import GateConfig, GateDecision, SplitDelta


def apply_gate(
    delta: SplitDelta,
    gate_config: GateConfig,
    cost_usd: float,
    duration_seconds: float,
) -> GateDecision:
    reasons: list[str] = []
    reject_reasons: list[str] = []
    overfitting_warning = False

    # Rule 1: Overfitting check (always on, warning only)
    if delta.train_pass_rate_delta > 0 and delta.val_pass_rate_delta < 0:
        overfitting_warning = True
        reasons.append(
            f"overfitting_warning: train pass_rate improved by {delta.train_pass_rate_delta:.4f} "
            f"but val pass_rate delta is {delta.val_pass_rate_delta:.4f}"
        )

    # Rule 2: min_improvement
    if delta.val_pass_rate_delta < gate_config.min_improvement:
        reject_reasons.append(
            f"min_improvement not met: val pass_rate delta is {delta.val_pass_rate_delta:.4f}, "
            f"required {gate_config.min_improvement:.4f}"
        )
    else:
        reasons.append(f"min_improvement met: val pass_rate delta {delta.val_pass_rate_delta:.4f} >= {gate_config.min_improvement:.4f}")

    # Rule 3: new_fails
    if not gate_config.allow_new_fails and len(delta.val.newly_failing) > 0:
        reject_reasons.append(
            f"newly failing in val not allowed: {delta.val.newly_failing}"
        )
    else:
        reasons.append(f"new_fails check passed: allow_new_fails={gate_config.allow_new_fails}, newly_failing={len(delta.val.newly_failing)}")

    # Rule 4: protected_cases (checks both train and val)
    degraded_protected: list[str] = []
    for case_id in gate_config.protected_case_ids:
        train_scores = delta.train.score_deltas.get(case_id, {})
        val_scores = delta.val.score_deltas.get(case_id, {})
        all_scores = {**train_scores, **val_scores}
        if all_scores and any(v < 0 for v in all_scores.values()):
            degraded_protected.append(case_id)
    if degraded_protected:
        reject_reasons.append(f"protected cases degraded: {degraded_protected}")
    elif gate_config.protected_case_ids:
        reasons.append(f"protected_cases check passed: all {len(gate_config.protected_case_ids)} protected cases ok")

    # Rule 5: cost_budget
    if gate_config.max_cost_usd is not None and cost_usd > gate_config.max_cost_usd:
        reject_reasons.append(
            f"cost ${cost_usd:.4f} exceeds budget ${gate_config.max_cost_usd:.4f}"
        )
    elif gate_config.max_cost_usd is not None:
        reasons.append(f"cost check passed: ${cost_usd:.4f} <= ${gate_config.max_cost_usd:.4f}")

    # Rule 6: duration
    if duration_seconds > gate_config.max_duration_seconds:
        reject_reasons.append(
            f"duration {duration_seconds:.2f}s exceeds limit {gate_config.max_duration_seconds}s"
        )
    else:
        reasons.append(f"duration check passed: {duration_seconds:.2f}s <= {gate_config.max_duration_seconds}s")

    if reject_reasons:
        all_reasons = reject_reasons + reasons
        return GateDecision(decision="REJECT", reasons=all_reasons, overfitting_warning=overfitting_warning)
    else:
        reasons.append("all gate rules passed")
        return GateDecision(decision="ACCEPT", reasons=reasons, overfitting_warning=overfitting_warning)
