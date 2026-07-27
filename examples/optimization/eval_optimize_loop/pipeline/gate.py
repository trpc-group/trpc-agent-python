# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Stage 5: Acceptance gate — decide whether to accept the optimized prompt."""

from __future__ import annotations

from .config import (
    BaselineResult,
    DeltaReport,
    GateConfig,
    GateDecision,
)


def decide(
    baseline_val: BaselineResult,
    delta: DeltaReport,
    gate_config: GateConfig,
    total_cost_usd: float = 0.0,
    delta_report_train: DeltaReport | None = None,
) -> GateDecision:
    """Run the composite acceptance gate.

    All checks must pass for the candidate to be accepted.

    Args:
        baseline_val: Baseline validation result.
        delta: Delta report from comparator (val set).
        gate_config: Gate threshold configuration.
        total_cost_usd: Total LLM cost across all stages.
        delta_report_train: Optional delta report for the train set, used
            to detect classic overfitting (train ↑ but val ↓).

    Returns:
        GateDecision with accepted flag, reason, and per-check results.
    """
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    warnings: list[str] = []

    # ── Check 1: min improvement on val set ──────────────────────
    checks["min_improvement"] = delta.delta >= gate_config.min_improvement
    if not checks["min_improvement"]:
        reasons.append(
            f"通过率提升不足: val delta={delta.delta:.3f}, "
            f"要求 >= {gate_config.min_improvement:.3f}"
        )

    # ── Check 2: no hard regression (PASS → FAIL) ────────────────
    hard_regressions = [
        d for d in delta.per_case if d.change_type == "newly_failing"
    ]
    checks["no_hard_regression"] = (
        not gate_config.no_hard_regression or len(hard_regressions) == 0
    )
    if not checks["no_hard_regression"]:
        reg_ids = [d.case_id for d in hard_regressions]
        reasons.append(
            f"存在硬回归 (PASS→FAIL): {reg_ids}"
        )

    # ── Check 3: key cases preserved ──────────────────────────────
    key_failed = []
    for d in delta.per_case:
        if d.case_id in gate_config.key_cases and d.candidate_status == "FAILED":
            key_failed.append(d.case_id)
    checks["key_cases_ok"] = len(key_failed) == 0
    if not checks["key_cases_ok"]:
        reasons.append(
            f"关键 case 未通过: {key_failed}"
        )

    # ── Check 4: cost budget ──────────────────────────────────────
    checks["cost_ok"] = total_cost_usd <= gate_config.cost_budget_usd
    if not checks["cost_ok"]:
        reasons.append(
            f"成本超出预算: ${total_cost_usd:.4f} > "
            f"${gate_config.cost_budget_usd:.4f}"
        )

    # ── Check 5: per-metric floor ─────────────────────────────────
    all_metrics_ok = True
    for d in delta.per_case:
        for metric, score in d.candidate_scores.items():
            floor = gate_config.per_metric_floor.get(metric, 0.0)
            if score < floor:
                all_metrics_ok = False
                reasons.append(
                    f"Case '{d.case_id}' metric '{metric}' score "
                    f"{score:.4f} < floor {floor:.4f}"
                )
    checks["per_metric_floor"] = all_metrics_ok

    # ── Check 6: Overfitting rejection (train ↑ but val ↓) ────────
    # Classic overfitting: candidate memorises training examples but
    # hurts generalisation → MUST REJECT.
    overfit_ok = True
    if (
        gate_config.reject_overfit
        and delta_report_train is not None
    ):
        train_delta = delta_report_train.delta
        val_delta = delta.delta
        train_improved = train_delta >= gate_config.overfit_train_gain
        val_degraded = val_delta <= gate_config.overfit_val_loss

        if train_improved and val_degraded:
            overfit_ok = False
            # Diagnose which train cases improved and which val cases regressed
            train_gain_cases = [
                d.case_id for d in delta_report_train.per_case
                if d.change_type in ("newly_passing", "improved")
            ]
            val_loss_cases = [
                d.case_id for d in delta.per_case
                if d.change_type in ("newly_failing", "degraded")
            ]
            reasons.append(
                f"过拟合（训练集提升但验证集退化）: "
                f"train delta={train_delta:+.3f}, val delta={val_delta:+.3f}. "
                f"Candidate prompt 在 {len(train_gain_cases)} 个训练 case 上表现提升，"
                f"但在 {len(val_loss_cases)} 个验证 case 上出现退化"
                f"（验证退化 IDs: {val_loss_cases[:10]}{'…' if len(val_loss_cases) > 10 else ''}）。"
                f"模型过度记忆了训练集模式，泛化能力下降 — 强制拒绝。"
            )
        elif train_delta > 0 and val_delta < 0:
            # Borderline case — flag a warning but don't block if thresholds not met
            warnings.append(
                f"潜在过拟合趋势: train delta={train_delta:+.3f} 但 "
                f"val delta={val_delta:+.3f}，请关注后续轮次是否恶化"
            )
    checks["no_overfit"] = overfit_ok

    # ── Additional overfitting / degradation warnings ─────────────
    if delta.delta > 0 and gate_config.overfitting_warning_threshold > 0:
        degraded_count = sum(
            1 for d in delta.per_case if d.change_type == "degraded"
        )
        if degraded_count > 0:
            warnings.append(
                f"验证集分数下降警告: {degraded_count} 个 case 出现分数下降，"
                f"请检查训练-验证泛化差距"
            )

    # Train-val gap at candidate time (if we have both deltas)
    if delta_report_train is not None:
        gap = delta_report_train.candidate_pass_rate - delta.candidate_pass_rate
        baseline_gap = (
            delta_report_train.baseline_pass_rate - delta.baseline_pass_rate
        )
        if (
            gate_config.overfitting_warning_threshold > 0
            and gap > gate_config.overfitting_warning_threshold
            and gap > baseline_gap + 0.05
        ):
            warnings.append(
                f"训练-验证泛化差距扩大: 候选 prompt 下 gap={gap:.1%} "
                f"（baseline gap={baseline_gap:.1%}），存在过拟合风险"
            )

    accepted = all(checks.values())

    if accepted:
        reason = "所有 Gate 条件均满足，建议接受候选 prompt"
    else:
        reason = "Gate 拒绝: " + "; ".join(reasons) if reasons else "未知原因"

    return GateDecision(
        accepted=accepted,
        reason=reason,
        checks=checks,
        warnings=warnings,
    )
