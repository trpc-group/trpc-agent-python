"""Phase 5: 接受策略 Gate。

根据 optimizer.json 中的 gate 配置，对候选 prompt 的验证结果进行
多条件判断，输出接受/拒绝决策。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from fake.fake_judge import PASS_THRESHOLD



@dataclass
class GateCheck:
    """单条 gate 检查结果"""
    name: str
    passed: bool
    description: str
    detail: str = ""


@dataclass
class GateDecision:
    """Gate 整体决策"""
    accepted: bool
    reason: str
    checks: list[GateCheck] = field(default_factory=list)
    strategy: str = "all_must_pass"

    @property
    def failed_checks(self) -> list[GateCheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def passed_checks(self) -> list[GateCheck]:
        return [c for c in self.checks if c.passed]




def _fmt_critical_detail(regressed: list[str], missing: list[str], unknown: list[str]) -> str:
    """Format critical-case check detail string.

    Emits all three categories so failures are never silently truncated:
    regressed (score dropped), missing (gone from candidate), unknown (absent
    from baseline ? config drift).
    """
    parts = []
    if regressed:
        parts.append(f"regressed: {regressed}")
    if missing:
        parts.append(f"missing: {missing}")
    if unknown:
        parts.append(f"unknown (not in baseline): {unknown}")
    return "; ".join(parts) if parts else "all critical cases stable"

class AcceptanceGate:
    """可配置的接受策略决策器。

    支持两种策略：
    - all_must_pass: 所有启用的规则都通过才接受
    - majority: 多数规则通过即接受

    5 条可配置规则（从 optimizer.json 读取）：
    1. total_score_improvement: 验证集总分提升 ≥ 阈值
    2. no_new_hard_fail: 不允许新增 hard fail
    3. critical_case_no_regress: 关键 case 不退步
    4. cost_within_budget: 成本不超预算
    5. overfit_detection: 过拟合检测（训练提升 + 验证退化 → 拒绝）
    """

    def __init__(self, gate_config: dict):
        """
        Args:
            gate_config: optimizer.json 中 "gate" 节的配置
        """
        self.rules = gate_config.get("rules", {})
        self.strategy = gate_config.get("acceptance_strategy", "all_must_pass")

    def decide(
        self,
        baseline_scores: dict[str, float],      # {case_id: score}
        candidate_scores: dict[str, float],      # {case_id: score}
        baseline_train_scores: Optional[dict[str, float]] = None,  # {case_id: score}
        candidate_train_scores: Optional[dict[str, float]] = None,  # {case_id: score}
        baseline_cost: float = 0.0,
        candidate_cost: float = 0.0,
        critical_case_ids: Optional[list[str]] = None,
    ) -> GateDecision:
        """执行 gate 决策。

        Returns:
            GateDecision: 包含决策结果和每条规则的检查详情
        """
        checks: list[GateCheck] = []

        # 1. 总分提升检查
        if self._rule_enabled("total_score_improvement"):
            checks.append(self._check_total_improvement(
                baseline_scores, candidate_scores
            ))

        # 2. 无新增 hard fail
        if self._rule_enabled("no_new_hard_fail"):
            checks.append(self._check_no_new_hard_fail(
                baseline_scores, candidate_scores
            ))

        # 3. 关键 case 不退步
        if self._rule_enabled("critical_case_no_regress"):
            checks.append(self._check_critical_cases(
                baseline_scores, candidate_scores, critical_case_ids or []
            ))

        # 4. 成本不超预算
        if self._rule_enabled("cost_within_budget"):
            checks.append(self._check_cost(baseline_cost, candidate_cost))

        # 5. 过拟合检测
        if self._rule_enabled("overfit_detection") and baseline_train_scores and candidate_train_scores:
            checks.append(self._check_overfit(
                baseline_train_scores, candidate_train_scores,
                baseline_scores, candidate_scores
            ))

        # decision: reject when no checks ran (all rules disabled/missing data)
        if self.strategy == "all_must_pass":
            accepted = len(checks) > 0 and all(c.passed for c in checks)
        elif self.strategy == "majority":
            # Strict majority: more than half must pass. Ties (exactly half) = reject.
            accepted = sum(1 for c in checks if c.passed) > len(checks) / 2
        else:
            raise ValueError(f"Unknown gate strategy: {self.strategy!r}. Expected 'all_must_pass' or 'majority'.")

        reason = self._build_reason(accepted, checks)
        return GateDecision(
            accepted=accepted,
            reason=reason,
            checks=checks,
            strategy=self.strategy,
        )

    # ── 各检查项 ────────────────────────────────────────

    def _check_total_improvement(
        self,
        baseline: dict[str, float],
        candidate: dict[str, float],
    ) -> "GateCheck":
        threshold = self.rules["total_score_improvement"].get("threshold", 0.03)
        base_avg = sum(baseline.values()) / len(baseline) if baseline else 0
        cand_avg = sum(candidate.values()) / len(candidate) if candidate else 0
        delta = cand_avg - base_avg
        passed = delta >= threshold
        return GateCheck(
            name="total_score_improvement",
            passed=passed,
            description=f"总分提升 ≥ {threshold:.0%}",
            detail=f"baseline={base_avg:.3f}, candidate={cand_avg:.3f}, delta={delta:+.3f}",
        )

    def _check_no_new_hard_fail(
        self,
        baseline: dict[str, float],
        candidate: dict[str, float],
    ) -> "GateCheck":
        max_new = self.rules["no_new_hard_fail"].get("max_new_fails", 0)
        # Case-level comparison: a case is a "new hard fail" iff it scores
        # below PASS_THRESHOLD in candidate AND was at/above PASS_THRESHOLD
        # in baseline.  Net count (cand_fails - base_fails) is incorrect:
        # swapping N fixed old-fails for N different new-fails would yield
        # new_fails=0 and silently pass.
        #
        # Cases NOT present in baseline (baseline.get returns None) are
        # skipped for new-fail counting: a case that was never evaluated in
        # baseline cannot be a "regression".  Callers that expand the evalset
        # should first re-baseline so new cases have a valid baseline score.
        new_fails = sum(
            1 for cid, score in candidate.items()
            if score < PASS_THRESHOLD and baseline.get(cid) is not None
            and baseline[cid] >= PASS_THRESHOLD
        )
        base_fails = sum(1 for s in baseline.values() if s < PASS_THRESHOLD)
        cand_fails = sum(1 for s in candidate.values() if s < PASS_THRESHOLD)
        passed = new_fails <= max_new
        return GateCheck(
            name="no_new_hard_fail",
            passed=passed,
            description=f"新增 hard fail ≤ {max_new}",
            detail=(
                f"baseline fails={base_fails}, candidate fails={cand_fails}, "
                f"new(case-level)={new_fails}"
                + (f", skipped_new_cases={sum(1 for cid in candidate if cid not in baseline)}"
                   if any(cid not in baseline for cid in candidate) else "")
            ),
        )
    def _check_critical_cases(
        self,
        baseline: dict[str, float],
        candidate: dict[str, float],
        critical_ids: list[str],
    ) -> "GateCheck":
        if not critical_ids:
            return GateCheck(
                name="critical_case_no_regress",
                passed=True,
                description="无关键 case 配置",
                detail="skipped: no critical case ids",
            )
        regressed = [
            cid for cid in critical_ids
            if cid in baseline and cid in candidate
            and candidate[cid] < baseline[cid] - 0.005
        ]
        missing = [cid for cid in critical_ids if cid not in candidate]
        unknown = [cid for cid in critical_ids if cid not in baseline]
        passed = len(regressed) == 0 and len(missing) == 0 and len(unknown) == 0
        return GateCheck(
            name="critical_case_no_regress",
            passed=passed,
            description="关键 case 不退步",
            detail=_fmt_critical_detail(regressed, missing, unknown),
        )

    def _check_cost(
        self,
        baseline_cost: float,
        candidate_cost: float,
    ) -> GateCheck:
        max_ratio = self.rules["cost_within_budget"].get("max_cost_ratio", 1.2)
        if baseline_cost <= 0:
            # Cost data is absent (e.g. real mode token_tracker not connected, or
            # BaselineCaseResult.cost defaults to 0.0).  Fail-closed: mark the gate
            # as skipped with passed=False so all_must_pass rejects the candidate
            # rather than silently accepting it without cost validation.
            return GateCheck(
                name="cost_within_budget",
                passed=False,
                description="cost with budget",
                detail="skipped: baseline cost data unavailable (token_tracker not connected?)",
            )
        ratio = candidate_cost / baseline_cost
        passed = ratio <= max_ratio
        return GateCheck(
            name="cost_within_budget",
            passed=passed,
            description=f"成本 ≤ {max_ratio:.0%}× baseline",
            detail=f"baseline={baseline_cost:.4f}, candidate={candidate_cost:.4f}, ratio={ratio:.2f}",
        )

    def _check_overfit(
        self,
        baseline_train: dict[str, float],
        candidate_train: dict[str, float],
        baseline_val: dict[str, float],
        candidate_val: dict[str, float],
    ) -> "GateCheck":
        train_avg_base = sum(baseline_train.values()) / len(baseline_train) if baseline_train else 0
        train_avg_cand = sum(candidate_train.values()) / len(candidate_train) if candidate_train else 0
        val_avg_base = sum(baseline_val.values()) / len(baseline_val) if baseline_val else 0
        val_avg_cand = sum(candidate_val.values()) / len(candidate_val) if candidate_val else 0

        train_improved = train_avg_cand > train_avg_base
        val_regressed = val_avg_cand < val_avg_base
        is_overfit = train_improved and val_regressed

        return GateCheck(
            name="overfit_detection",
            passed=not is_overfit,
            description="训练集提升 + 验证集退化 → 拒绝 (fake mode: simulated)",
            detail=(
                f"train: {train_avg_base:.3f}→{train_avg_cand:.3f} "
                f"({'improved' if train_improved else 'not improved'}), "
                f"val: {val_avg_base:.3f}→{val_avg_cand:.3f} "
                f"({'regressed' if val_regressed else 'stable'})"
            ),
        )

    # ── 辅助方法 ────────────────────────────────────────

    def _rule_enabled(self, rule_name: str) -> bool:
        rule = self.rules.get(rule_name, {})
        return rule.get("enabled", False)

    @staticmethod
    def _build_reason(accepted: bool, checks: list[GateCheck]) -> str:
        if accepted:
            return "所有 gate 检查通过，接受此候选 prompt"
        failed = [c for c in checks if not c.passed]
        reasons = [f"{c.name}: {c.detail}" for c in failed]
        return "拒绝候选 — " + "; ".join(reasons)


def load_gate_config(config_path: str | Path) -> dict:
    """从 optimizer.json 加载 gate 配置"""
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("gate", {})
