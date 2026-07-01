"""Phase 5 Gate 单元测试"""

import pytest
from src.gate import AcceptanceGate, GateDecision


class TestGateAcceptImproved:
    """场景：候选全面改善 → 应接受"""

    def test_accepts_improved_candidate(
        self, gate_config, sample_baseline_scores, sample_candidate_scores
    ):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores=sample_candidate_scores,
            baseline_cost=0.10,
            candidate_cost=0.11,
        )
        assert decision.accepted, f"应接受但被拒绝: {decision.reason}"
        assert len(decision.checks) >= 3  # 至少检查 total_score / hard_fail / cost
        assert all(c.passed for c in decision.checks), \
            [f"{c.name}: {c.detail}" for c in decision.failed_checks]


class TestGateRejectRegressed:
    """场景：候选退化 → 应拒绝"""

    def test_rejects_regressed_candidate(
        self, gate_config, sample_baseline_scores, sample_regressed_scores
    ):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores=sample_regressed_scores,
            baseline_cost=0.10,
            candidate_cost=0.09,
        )
        assert not decision.accepted, "退化候选应被拒绝"
        assert any(not c.passed for c in decision.checks)


class TestGateOverfitDetection:
    """场景：过拟合检测"""

    def test_rejects_overfit(
        self, gate_config
    ):
        """训练集提升 + 验证集退化 → 拒绝"""
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores={"v1": 0.80, "v2": 0.75},
            candidate_scores={"v1": 0.72, "v2": 0.70},     # 验证集退化
            baseline_train_scores={"t1": 0.50, "t2": 0.45},
            candidate_train_scores={"t1": 0.80, "t2": 0.75},  # 训练集提升
        )
        assert not decision.accepted, "过拟合应被拒绝"
        overfit_check = next(
            (c for c in decision.checks if c.name == "overfit_detection"), None
        )
        assert overfit_check is not None
        assert not overfit_check.passed

    def test_accepts_no_overfit(
        self, gate_config
    ):
        """训练集和验证集都提升 → 接受"""
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores={"v1": 0.70, "v2": 0.65},
            candidate_scores={"v1": 0.85, "v2": 0.80},      # 都提升
            baseline_train_scores={"t1": 0.50},
            candidate_train_scores={"t1": 0.80},             # 都提升
        )
        overfit_check = next(
            (c for c in decision.checks if c.name == "overfit_detection"), None
        )
        assert overfit_check is not None
        assert overfit_check.passed, f"不过拟合应通过: {overfit_check.detail}"


class TestGateCriticalCases:
    """场景：关键 case 不退步"""

    def test_rejects_critical_regression(
        self, gate_config, sample_baseline_scores
    ):
        gate = AcceptanceGate(gate_config)
        # val_001 是关键 case，从 0.95 退化到 0.80
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores={"val_001": 0.80, "val_002": 0.90, "val_003": 0.80},
            critical_case_ids=["val_001"],
        )
        critical_check = next(
            (c for c in decision.checks if c.name == "critical_case_no_regress"), None
        )
        assert critical_check is not None
        assert not critical_check.passed


class TestGateCostBudget:
    """场景：成本超预算"""

    def test_rejects_over_budget(self, gate_config, sample_baseline_scores, sample_candidate_scores):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores=sample_baseline_scores,
            candidate_scores=sample_candidate_scores,
            baseline_cost=0.10,
            candidate_cost=0.15,  # 1.5× → 超过 1.2× 阈值
        )
        cost_check = next(
            (c for c in decision.checks if c.name == "cost_within_budget"), None
        )
        assert cost_check is not None
        assert not cost_check.passed


class TestGateEdgeCases:
    """边界场景"""

    def test_empty_scores(self, gate_config):
        gate = AcceptanceGate(gate_config)
        decision = gate.decide(
            baseline_scores={},
            candidate_scores={},
        )
        # 总分提升 0.0 小于阈值 0.03 → 应失败
        total_check = next(
            (c for c in decision.checks if c.name == "total_score_improvement"), None
        )
        assert total_check is not None
        assert not total_check.passed

    def test_majority_strategy(self):
        """majority 策略：多数通过即接受"""
        config = {
            "rules": {
                "total_score_improvement": {"enabled": True, "threshold": 0.03},
                "no_new_hard_fail": {"enabled": True, "max_new_fails": 0},
                "cost_within_budget": {"enabled": True, "max_cost_ratio": 1.2},
            },
            "acceptance_strategy": "majority",
        }
        gate = AcceptanceGate(config)
        # 总分提升不达标（失败），但没有新 hard fail（通过），成本不超标（通过）→ 2/3 → 接受
        decision = gate.decide(
            baseline_scores={"v1": 0.80, "v2": 0.75},
            candidate_scores={"v1": 0.81, "v2": 0.76},  # 仅 +0.01 < 0.03
            baseline_cost=0.10,
            candidate_cost=0.10,
        )
        assert decision.accepted
        assert decision.strategy == "majority"
