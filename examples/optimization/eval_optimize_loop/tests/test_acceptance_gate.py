"""接受门控（Stage 5）单元测试。

测试 AcceptanceGate 的各项检查逻辑，覆盖：
  - 提升阈值检查（通过/失败）
  - 回归检查（禁止回归/允许限制数量）
  - 关键 case 检查
  - 成本预算检查
  - 多检查组合场景

门控核心理念：安全优先——任一检查失败则拒绝候选。
"""

import pytest

from pipeline._models import AcceptanceGateConfig, GateCheckResult, GateDecision


class TestGateCheckResult:
    """GateCheckResult 模型的单元测试。"""

    def test_passed_check(self):
        """通过检查的 GateCheckResult 应正确记录信息。"""
        check = GateCheckResult(check_name="improvement", passed=True, detail="+0.2 提升")
        assert check.passed is True
        assert check.check_name == "improvement"

    def test_failed_check(self):
        """失败检查的 GateCheckResult 应包含退化 case 信息。"""
        check = GateCheckResult(check_name="no_regressions", passed=False, detail="1 个退化: val_003")
        assert check.passed is False
        assert "val_003" in check.detail


class TestAcceptanceGate:
    """AcceptanceGate 类的单元测试。

    测试所有门控检查项在不同配置下的行为。
    """

    @pytest.fixture
    def gate(self):
        """导入 AcceptanceGate 类作为 fixture。"""
        from pipeline._stage_acceptance_gate import AcceptanceGate
        return AcceptanceGate

    def build_gate_config(self, **overrides):
        """构建门控配置的辅助方法，支持覆盖默认值。

        Args:
            **overrides: 要覆盖的配置项（如 min_improvement_threshold=0.1）。

        Returns:
            AcceptanceGateConfig 实例。
        """
        defaults = {
            "min_improvement_threshold": 0.0,
            "no_new_hard_failures": True,
            "max_regressions_allowed": 0,
            "critical_case_ids": [],
            "max_cost_budget": 0.0,
        }
        defaults.update(overrides)
        return AcceptanceGateConfig(**defaults)

    def test_accept_when_improvement_above_threshold(self, gate):
        """提升超过阈值时，候选应被接受。"""
        config = self.build_gate_config(min_improvement_threshold=0.1)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "PASSED"},
            total_cost=0.0,
        )

        assert decision.accepted is True
        assert decision.improvement == pytest.approx(0.33)

    def test_reject_when_improvement_below_threshold(self, gate):
        """提升低于阈值时，候选应被拒绝。"""
        config = self.build_gate_config(min_improvement_threshold=0.2)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.5,  # 提升 0.17，低于阈值 0.2
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "PASSED"},
            total_cost=0.0,
        )

        assert decision.accepted is False
        assert "improvement" in decision.reason.lower()

    def test_reject_when_new_hard_failures_present(self, gate):
        """存在新增 hard failure（基线通过→候选失败）时应被拒绝。"""
        config = self.build_gate_config(no_new_hard_failures=True)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.5,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "FAILED"},  # val_003 退化
            total_cost=0.0,
        )

        assert decision.accepted is False
        assert "val_003" in decision.regressed_case_ids
        assert "regressed" in decision.reason.lower() or "退化" in decision.reason

    def test_accept_when_regressions_within_limit(self, gate):
        """回归数量在允许范围内时，候选应被接受。"""
        config = self.build_gate_config(no_new_hard_failures=False, max_regressions_allowed=1)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "FAILED"},  # 1 个回归
            total_cost=0.0,
        )

        # 1 个回归（val_003），在允许范围内
        assert decision.accepted is True

    def test_accept_when_max_regressions_overrides_no_new_hard_failures(self, gate):
        """max_regressions_allowed > 0 时应覆盖 no_new_hard_failures 的严格限制（_models.py 契约）。"""
        config = self.build_gate_config(no_new_hard_failures=True, max_regressions_allowed=2)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED", "val_004": "PASSED", "val_005": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "FAILED", "val_004": "PASSED", "val_005": "PASSED"},  # 1 个回归
            total_cost=0.0,
        )

        # 1 个回归 ≤ 上限 2，即使 no_new_hard_failures=True 也应接受
        assert decision.accepted is True

    def test_reject_when_regressions_exceed_overridden_limit(self, gate):
        """max_regressions_allowed > 0 时，回归数超过上限仍应拒绝。"""
        config = self.build_gate_config(no_new_hard_failures=True, max_regressions_allowed=2)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "PASSED", "val_003": "PASSED", "val_004": "PASSED", "val_005": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "FAILED", "val_004": "FAILED", "val_005": "PASSED"},  # 3 个回归
            total_cost=0.0,
        )

        assert decision.accepted is False
        assert len(decision.regressed_case_ids) == 3

    def test_reject_when_regressions_exceed_limit(self, gate):
        """回归数量超过上限时，候选应被拒绝。"""
        config = self.build_gate_config(no_new_hard_failures=False, max_regressions_allowed=0)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.5,
            candidate_pass_rate=0.66,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "PASSED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "PASSED"},
            total_cost=0.0,
        )

        assert decision.accepted is False
        assert len(decision.regressed_case_ids) == 1

    def test_reject_when_critical_case_fails(self, gate):
        """关键 case 失败时，即使有改善也应拒绝。"""
        config = self.build_gate_config(
            min_improvement_threshold=0.0,
            critical_case_ids=["val_003"],
        )
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "PASSED", "val_003": "FAILED"},  # 关键 case 失败
            total_cost=0.0,
        )

        assert decision.accepted is False
        assert "critical" in decision.reason.lower()

    def test_reject_when_cost_exceeds_budget(self, gate):
        """成本超出预算时，候选应被拒绝。"""
        config = self.build_gate_config(max_cost_budget=5.0)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "PASSED", "val_003": "PASSED"},
            total_cost=10.0,  # 超出 5.0 预算
        )

        assert decision.accepted is False
        assert "cost" in decision.reason.lower() or "budget" in decision.reason.lower() or "成本" in decision.reason or "预算" in decision.reason

    def test_all_checks_passing(self, gate):
        """所有检查通过时，候选应被接受并包含完整检查列表。"""
        config = self.build_gate_config(
            min_improvement_threshold=0.0,
            no_new_hard_failures=True,
            critical_case_ids=["val_003"],
            max_cost_budget=10.0,
        )
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=1.0,
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "PASSED", "val_003": "PASSED"},
            total_cost=5.0,
        )

        assert decision.accepted is True
        assert len(decision.checks) >= 4  # improvement, regressions, critical, cost

    def test_no_improvement_no_regressions(self, gate):
        """无提升但无退化时：threshold 为 0 时仍被拒绝（提升未超过阈值）。

        注意：提升必须严格大于阈值才能通过。
        """
        config = self.build_gate_config(min_improvement_threshold=0.0)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.33,  # 无变化
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            total_cost=0.0,
        )

        assert decision.accepted is False  # 无提升（0 ≤ 0 不通过）
        assert decision.improvement == 0.0

    def test_accept_with_tiny_improvement(self, gate):
        """阈值为 0 且有微小提升时，应接受候选。"""
        config = self.build_gate_config(min_improvement_threshold=0.0)
        gate_instance = gate(config)

        decision = gate_instance.evaluate(
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.34,  # 微小提升
            baseline_case_statuses={"val_001": "FAILED", "val_002": "FAILED", "val_003": "PASSED"},
            candidate_case_statuses={"val_001": "PASSED", "val_002": "FAILED", "val_003": "PASSED"},
            total_cost=0.0,
        )

        assert decision.accepted is True
        assert decision.improvement > 0
