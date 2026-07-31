"""Stage 5: 接受门控 — 评估候选 prompt 是否满足接受条件。

本阶段实现安全优先的候选 prompt 接受策略，采用多检查 AND 逻辑——
任一检查失败则拒绝候选。检查项包括：

  1. 提升阈值检查（improvement_threshold）:
     验证集 pass rate 提升必须 ≥ 配置阈值

  2. 回归检查（regression_check / no_new_hard_failures）:
     基线通过的 case 候选不能失败（no_new_hard_failures）
     或回归数量不超过上限（max_regressions_allowed）

  3. 关键 case 检查（critical_cases）:
     指定的关键 case 必须在候选评估中通过

  4. 成本预算检查（cost_budget）:
     LLM 调用总成本不超过预算上限

设计理念：安全优先——即使候选在训练集上大幅提升，如果导致验证集
关键 case 退化，也会被拒绝。

调用方式：
    gate = AcceptanceGate(config)
    decision = gate.evaluate(
        baseline_pass_rate=0.33,
        candidate_pass_rate=0.66,
        baseline_case_statuses={"val_001": "FAILED", "val_002": "PASSED"},
        candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED"},
        total_cost=0.01,
    )
"""

from __future__ import annotations

from pipeline._models import AcceptanceGateConfig, GateCheckResult, GateDecision


class AcceptanceGate:
    """接受门控：按可配置标准评估候选 prompt 是否可接受。

    通过 evaluate() 方法运行所有门控检查，返回 GateDecision。
    每个检查生成一个 GateCheckResult，包含检查名称、是否通过和详细说明。
    """

    def __init__(self, config: AcceptanceGateConfig) -> None:
        """初始化接受门控。

        Args:
            config: AcceptanceGateConfig 实例，包含所有检查项的配置参数。
        """
        self._config = config

    def evaluate(
        self,
        *,
        baseline_pass_rate: float,
        candidate_pass_rate: float,
        baseline_case_statuses: dict[str, str],
        candidate_case_statuses: dict[str, str],
        total_cost: float = 0.0,
    ) -> GateDecision:
        """运行所有门控检查并返回决策。

        Args:
            baseline_pass_rate: 基线验证集 pass rate。
            candidate_pass_rate: 候选验证集 pass rate。
            baseline_case_statuses: eval_id → 基线状态的映射。
            candidate_case_statuses: eval_id → 候选状态的映射。
            total_cost: LLM 调用总成本（USD）。

        Returns:
            GateDecision: 包含接受/拒绝决定、原因和所有检查结果。
        """
        checks: list[GateCheckResult] = []
        improvement = candidate_pass_rate - baseline_pass_rate

        # 找出发生退化的 case（PASSED → FAILED）
        actual_regressed = []
        for eval_id in baseline_case_statuses:
            if (baseline_case_statuses[eval_id] == "PASSED"
                    and candidate_case_statuses.get(eval_id) == "FAILED"):
                actual_regressed.append(eval_id)

        # 执行各项检查
        self._check_improvement(improvement, checks)
        self._check_regressions(actual_regressed, checks)
        self._check_critical_cases(candidate_case_statuses, checks)
        self._check_cost_budget(total_cost, checks)

        # 所有检查都必须通过
        all_passed = all(c.passed for c in checks)
        reason = self._build_reason(all_passed, checks, improvement, actual_regressed)

        return GateDecision(
            accepted=all_passed,
            reason=reason,
            checks=checks,
            baseline_pass_rate=baseline_pass_rate,
            candidate_pass_rate=candidate_pass_rate,
            improvement=improvement,
            regressed_case_ids=actual_regressed,
        )

    def _check_improvement(
        self, improvement: float, checks: list[GateCheckResult]
    ) -> None:
        """检查 pass rate 提升是否达到阈值。

        提升 > 阈值 → 通过；提升 ≤ 阈值 → 失败。
        """
        threshold = self._config.min_improvement_threshold
        if improvement > threshold:
            checks.append(GateCheckResult(
                check_name="improvement_threshold",
                passed=True,
                detail=f"提升 {improvement:.4f} > 阈值 {threshold}",
            ))
        else:
            checks.append(GateCheckResult(
                check_name="improvement_threshold",
                passed=False,
                detail=f"提升 {improvement:.4f} ≤ 阈值 {threshold}",
            ))

    def _check_regressions(
        self,
        regressed_ids: list[str],
        checks: list[GateCheckResult],
    ) -> None:
        """检查回归数量是否在允许范围内。

        逻辑（max_regressions_allowed > 0 时覆盖 no_new_hard_failures 的
        严格限制，见 _models.py 字段契约）：
        - 回归数超过 max_regressions_allowed → 失败
        - 严格模式（no_new_hard_failures=True 且 max_regressions_allowed==0）
          下存在任何回归 → 失败
        - 否则 → 通过
        """
        num_regressions = len(regressed_ids)

        if num_regressions > self._config.max_regressions_allowed:
            if self._config.max_regressions_allowed == 0 and self._config.no_new_hard_failures:
                # 严格模式（上限 0 = 未配置放宽）: 报告为 no_new_hard_failures
                checks.append(GateCheckResult(
                    check_name="no_new_hard_failures",
                    passed=False,
                    detail=f"{num_regressions} 个 case 退化: {', '.join(regressed_ids)}",
                ))
            else:
                checks.append(GateCheckResult(
                    check_name="regression_limit",
                    passed=False,
                    detail=f"{num_regressions} 个退化超过上限 {self._config.max_regressions_allowed}",
                ))
        else:
            checks.append(GateCheckResult(
                check_name="regression_check",
                passed=True,
                detail=f"{num_regressions} 个退化，在上限 {self._config.max_regressions_allowed} 以内",
            ))

    def _check_critical_cases(
        self,
        candidate_statuses: dict[str, str],
        checks: list[GateCheckResult],
    ) -> None:
        """验证所有关键 case 是否通过。

        如果未配置关键 case，该检查自动通过。
        """
        critical_ids = self._config.critical_case_ids
        if not critical_ids:
            checks.append(GateCheckResult(
                check_name="critical_cases",
                passed=True,
                detail="未配置关键 case。",
            ))
            return

        failed_critical = [
            cid for cid in critical_ids
            if candidate_statuses.get(cid) != "PASSED"
        ]
        if failed_critical:
            checks.append(GateCheckResult(
                check_name="critical_cases",
                passed=False,
                detail=f"关键 case 失败: {', '.join(failed_critical)}",
            ))
        else:
            checks.append(GateCheckResult(
                check_name="critical_cases",
                passed=True,
                detail=f"全部 {len(critical_ids)} 个关键 case 通过。",
            ))

    def _check_cost_budget(
        self, total_cost: float, checks: list[GateCheckResult]
    ) -> None:
        """检查 LLM 调用成本是否在预算内。

        如果 max_cost_budget=0（不限制），该检查自动通过。
        """
        budget = self._config.max_cost_budget
        if budget <= 0.0:
            checks.append(GateCheckResult(
                check_name="cost_budget",
                passed=True,
                detail="未配置成本预算。",
            ))
        elif total_cost <= budget:
            checks.append(GateCheckResult(
                check_name="cost_budget",
                passed=True,
                detail=f"成本 ${total_cost:.2f} 在预算 ${budget:.2f} 以内",
            ))
        else:
            checks.append(GateCheckResult(
                check_name="cost_budget",
                passed=False,
                detail=f"成本 ${total_cost:.2f} 超出预算 ${budget:.2f}",
            ))

    @staticmethod
    def _build_reason(
        all_passed: bool,
        checks: list[GateCheckResult],
        improvement: float,
        regressed_ids: list[str],
    ) -> str:
        """生成人类可读的决策原因字符串。

        Args:
            all_passed: 所有检查是否都通过。
            checks: 所有检查结果列表。
            improvement: pass rate 变化。
            regressed_ids: 退化的 case ID 列表。

        Returns:
            原因描述字符串。
        """
        if all_passed:
            return (
                f"接受: 全部 {len(checks)} 项检查通过。"
                f"改善: {improvement:+.4f}。"
            )
        failed = [c for c in checks if not c.passed]
        reasons = [f"{c.check_name}: {c.detail}" for c in failed]
        return f"拒绝: {'; '.join(reasons)}"
