"""Gate 决策：可配置 AND 规则，三态（accept/reject/needs_review）+ 过拟合三重检测。

见 DESIGN.md §4.3 / §8。
- 安全类失败（overfit / hard fail / critical 回归 / 预算）→ reject
- 软类失败（提升不足 / tie）→ needs_review（除非 tie_policy=reject）
- 全过 → accept
"""
from __future__ import annotations

from .comparator import CandidateDelta
from .config import GateConfig
from .models import GateCheck, GateDecisionResult

_EPS = 1e-9
# 视为「安全类」的 check 名（任一失败 → reject，而非 needs_review）
_SAFETY_CHECKS = {
    "no_overfit",
    "no_new_hard_fails",
    "no_critical_regression",
    "no_case_regression",
    "budget_duration",
    "budget_metric_calls",
    "evaluation_complete",
    "validation_pass_rate_not_worse",
    "tie_policy",  # tie = 候选无任何改进，应明确 reject 而非 needs_review
}


def evaluate_gate(
    delta: CandidateDelta,
    cfg: GateConfig,
    duration_seconds: float,
    metric_calls: int,
) -> GateDecisionResult:
    """对单个候选做 gate 决策。baseline 与 candidate 的对比已编码在 delta 里。"""
    checks: list[GateCheck] = []

    # --- 过拟合三重检测（§4.3）---
    train_pr_d = delta.train.pass_rate_delta
    val_pr_d = delta.validation.pass_rate_delta
    explicit_overfit = train_pr_d > _EPS and val_pr_d <= _EPS
    # 泛化缺口只在 val 未达标时才算过拟合信号——否则会误伤
    # 「val 也在提升、只是 train 提升更大」的健康候选（baseline train 通常更差，提升空间更大）。
    val_improved = delta.validation.average_score_delta >= cfg.min_validation_score_delta - _EPS
    train_minus_val = delta.train.average_score_delta - delta.validation.average_score_delta
    gen_gap = (not val_improved) and train_minus_val > cfg.generalization_gap_threshold
    # 趋势检测需要多轮 RoundRecord，单次 fixture 评估无趋势信号；此处仅前两道
    overfit = bool(cfg.overfitting_enabled and (explicit_overfit or gen_gap))

    checks.append(
        GateCheck(
            check="no_overfit",
            passed=not overfit,
            actual={
                "explicit": explicit_overfit,
                "generalization_gap": gen_gap,
                "train_pr_delta": train_pr_d,
                "val_pr_delta": val_pr_d,
            },
            expected="no train↑-with-val↓ nor excessive generalization gap",
            reason="overfitting detected" if overfit else "ok",
        ))

    # --- hard fail / 回归 ---
    new_fails = len(delta.buckets.new_fail)
    checks.append(
        GateCheck(
            check="no_new_hard_fails",
            passed=new_fails <= cfg.max_new_hard_fails,
            actual=new_fails,
            expected=cfg.max_new_hard_fails,
            reason=f"{new_fails} newly failing cases" if new_fails else "ok",
        ))

    critical_hit = [
        cid for cid in cfg.critical_case_ids if cid in delta.buckets.new_fail or cid in delta.buckets.regressed
    ]
    checks.append(
        GateCheck(
            check="no_critical_regression",
            passed=not critical_hit,
            actual=critical_hit,
            expected=[],
            reason=f"critical cases regressed: {critical_hit}" if critical_hit else "ok",
        ))

    checks.append(
        GateCheck(
            check="no_case_regression",
            passed=len(delta.buckets.regressed) == 0,
            actual=delta.buckets.regressed,
            expected=[],
            reason=f"regressed cases: {delta.buckets.regressed}" if delta.buckets.regressed else "ok",
        ))

    # --- 验证集提升 ---
    checks.append(
        GateCheck(
            check="validation_score_improved",
            passed=delta.validation.average_score_delta >= cfg.min_validation_score_delta - _EPS,
            actual=delta.validation.average_score_delta,
            expected=cfg.min_validation_score_delta,
            reason="insufficient validation gain"
            if delta.validation.average_score_delta < cfg.min_validation_score_delta - _EPS else "ok",
        ))
    checks.append(
        GateCheck(
            check="validation_pass_rate_not_worse",
            passed=val_pr_d >= -_EPS,
            actual=val_pr_d,
            expected=0.0,
            reason="validation pass rate dropped" if val_pr_d < -_EPS else "ok",
        ))

    # --- 预算 ---
    checks.append(
        GateCheck(
            check="budget_duration",
            passed=duration_seconds <= cfg.max_duration_seconds,
            actual=duration_seconds,
            expected=cfg.max_duration_seconds,
            reason="duration over budget" if duration_seconds > cfg.max_duration_seconds else "ok",
        ))
    checks.append(
        GateCheck(
            check="budget_metric_calls",
            passed=metric_calls <= cfg.max_metric_calls,
            actual=metric_calls,
            expected=cfg.max_metric_calls,
            reason="metric calls over budget" if metric_calls > cfg.max_metric_calls else "ok",
        ))

    # --- tie ---
    is_tie = abs(delta.validation.average_score_delta) < _EPS and abs(val_pr_d) < _EPS
    if is_tie and cfg.tie_policy == "reject":
        checks.append(
            GateCheck(
                check="tie_policy",
                passed=False,
                actual="no change",
                expected="improvement",
                reason="candidate identical to baseline (tie) → reject per tie_policy",
            ))
    else:
        checks.append(GateCheck(check="tie_policy", passed=True, actual="ok", expected="ok", reason="ok"))

    # --- 汇总决策 ---
    failed = [c for c in checks if not c.passed]
    safety_failed = [c for c in failed if c.check in _SAFETY_CHECKS]
    overfit_or_critical = overfit or bool(critical_hit)

    if not failed:
        decision = "accept"
    elif safety_failed or overfit_or_critical:
        decision = "reject"
    else:
        decision = "needs_review"

    risk = "high" if overfit_or_critical else ("medium" if failed else "low")
    return GateDecisionResult(
        decision=decision,  # type: ignore[arg-type]
        accepted=(decision == "accept"),
        overfitting_detected=overfit,
        risk_level=risk,  # type: ignore[arg-type]
        checks=checks,
    )
