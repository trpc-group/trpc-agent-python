"""流水线 Pydantic 数据模型单元测试。

测试 _models.py 中所有数据模型的：
  - 默认值
  - 字段验证（范围、类型约束）
  - JSON 序列化/反序列化往返
"""

import json

import pytest
from pydantic import ValidationError

from pipeline._models import (
    AcceptanceGateConfig,
    CriticalCaseConfig,
    FailureAttributionReport,
    GateCheckResult,
    GateDecision,
    OptimizationExecutionReport,
    PerCaseDelta,
    PerCaseScore,
    PipelineReport,
)


class TestAcceptanceGateConfig:
    """AcceptanceGateConfig 模型测试。"""

    def test_default_config(self):
        """默认配置应有合理的安全默认值。"""
        config = AcceptanceGateConfig()
        assert config.min_improvement_threshold == 0.0
        assert config.no_new_hard_failures is True    # 默认禁止退化
        assert config.max_regressions_allowed == 0
        assert config.critical_case_ids == []
        assert config.max_cost_budget == 0.0            # 默认不限制预算

    def test_full_config(self):
        """完整配置应正确存储所有字段。"""
        config = AcceptanceGateConfig(
            min_improvement_threshold=0.1,
            no_new_hard_failures=False,
            max_regressions_allowed=2,
            critical_case_ids=["case_001", "case_002"],
            max_cost_budget=5.0,
        )
        assert config.min_improvement_threshold == 0.1
        assert config.no_new_hard_failures is False
        assert config.max_regressions_allowed == 2
        assert config.critical_case_ids == ["case_001", "case_002"]
        assert config.max_cost_budget == 5.0

    def test_invalid_threshold_negative(self):
        """负阈值应触发验证错误（ge=0.0 约束）。"""
        with pytest.raises(ValidationError):
            AcceptanceGateConfig(min_improvement_threshold=-0.1)

    def test_invalid_threshold_above_one(self):
        """超过 1.0 的阈值应触发验证错误（le=1.0 约束）。"""
        with pytest.raises(ValidationError):
            AcceptanceGateConfig(min_improvement_threshold=1.5)

    def test_invalid_negative_regressions(self):
        """负回归数应触发验证错误（ge=0 约束）。"""
        with pytest.raises(ValidationError):
            AcceptanceGateConfig(max_regressions_allowed=-1)

    def test_invalid_negative_budget(self):
        """负预算应触发验证错误（ge=0.0 约束）。"""
        with pytest.raises(ValidationError):
            AcceptanceGateConfig(max_cost_budget=-1.0)

    def test_roundtrip_json(self):
        """JSON 序列化→反序列化应保持数据不变。"""
        config = AcceptanceGateConfig(
            min_improvement_threshold=0.05,
            no_new_hard_failures=True,
            max_regressions_allowed=1,
            critical_case_ids=["val_003"],
            max_cost_budget=10.0,
        )
        data = config.model_dump()
        restored = AcceptanceGateConfig(**data)
        assert restored.min_improvement_threshold == 0.05
        assert restored.no_new_hard_failures is True
        assert restored.critical_case_ids == ["val_003"]


class TestGateDecision:
    """GateDecision 模型测试。"""

    def test_accept_decision(self):
        """接受决策应完整记录所有检查结果。"""
        decision = GateDecision(
            accepted=True,
            reason="全部检查通过",
            checks=[
                GateCheckResult(check_name="improvement", passed=True, detail="+0.2"),
                GateCheckResult(check_name="no_regressions", passed=True, detail="0 个退化"),
            ],
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            improvement=0.33,
            regressed_case_ids=[],
        )
        assert decision.accepted is True
        assert len(decision.checks) == 2

    def test_reject_decision(self):
        """拒绝决策应记录退化 case 信息。"""
        decision = GateDecision(
            accepted=False,
            reason="检测到 val_003 退化",
            checks=[
                GateCheckResult(check_name="improvement", passed=True, detail="+0.33"),
                GateCheckResult(
                    check_name="no_regressions",
                    passed=False,
                    detail="1 个 case 退化: val_003",
                ),
            ],
            baseline_pass_rate=0.33,
            candidate_pass_rate=0.66,
            improvement=0.33,
            regressed_case_ids=["val_003"],
        )
        assert decision.accepted is False
        assert decision.regressed_case_ids == ["val_003"]

    def test_roundtrip_json(self):
        """JSON 序列化/反序列化往返测试。"""
        decision = GateDecision(
            accepted=True,
            reason="ok",
            checks=[GateCheckResult(check_name="test", passed=True, detail="ok")],
            baseline_pass_rate=0.5,
            candidate_pass_rate=1.0,
            improvement=0.5,
            regressed_case_ids=[],
        )
        json_str = decision.model_dump_json()
        restored = GateDecision.model_validate_json(json_str)
        assert restored.accepted == decision.accepted
        assert restored.improvement == 0.5


class TestPerCaseDelta:
    """PerCaseDelta 模型测试。"""

    def test_improvement_delta(self):
        """FAILED→PASSED 的 delta 应正确记录分数变化。"""
        delta = PerCaseDelta(
            eval_id="val_001",
            scenario="optimizable_success",
            baseline_status="FAILED",
            candidate_status="PASSED",
            baseline_scores={"final_response_avg_score": 0.0},
            candidate_scores={"final_response_avg_score": 1.0},
            score_delta={"final_response_avg_score": 1.0},
            transition="FAILED->PASSED",
        )
        assert delta.transition == "FAILED->PASSED"
        assert delta.score_delta["final_response_avg_score"] == 1.0

    def test_regression_delta(self):
        """PASSED→FAILED 的 delta 应正确记录负向变化。"""
        delta = PerCaseDelta(
            eval_id="val_003",
            scenario="optimization_regression",
            baseline_status="PASSED",
            candidate_status="FAILED",
            baseline_scores={"final_response_avg_score": 1.0},
            candidate_scores={"final_response_avg_score": 0.0},
            score_delta={"final_response_avg_score": -1.0},
            transition="PASSED->FAILED",
        )
        assert delta.transition == "PASSED->FAILED"


class TestPipelineReport:
    """PipelineReport 顶层模型测试。"""

    def test_empty_report(self):
        """空报告应有正确的默认值。"""
        report = PipelineReport()
        assert report.pipeline_version == "1.0.0"
        assert report.overall_verdict == ""
        assert report.case_deltas == []

    def test_full_report_roundtrip(self):
        """完整报告的 JSON 序列化/反序列化往返测试。

        验证包含所有阶段输出的报告可以正确序列化和恢复。
        """
        report = PipelineReport(
            pipeline_version="1.0.0",
            timestamp="2026-01-01T00:00:00",
            pipeline_duration_seconds=30.0,
            demo_mode=True,
            baseline_train=None,
            baseline_val=None,
            failure_attribution=FailureAttributionReport(
                total_cases_evaluated=6,
                total_failed=4,
                clusters={"final_response_mismatch": ["train_001", "val_001"]},
                per_case_categories={"train_001": ["final_response_mismatch"]},
                summary="4/6 个 case 失败。",
            ),
            optimization_execution=OptimizationExecutionReport(
                algorithm="demo",
                status="SUCCEEDED",
                total_rounds=1,
                baseline_pass_rate=0.33,
                best_pass_rate=0.66,
                pass_rate_improvement=0.33,
                duration_seconds=10.0,
                total_llm_cost=0.0,
                best_prompts={"system_prompt": "优化后的 prompt 内容。"},
            ),
            candidate_validation=None,
            case_deltas=[
                PerCaseDelta(
                    eval_id="val_001",
                    scenario="optimizable_success",
                    baseline_status="FAILED",
                    candidate_status="PASSED",
                    baseline_scores={},
                    candidate_scores={},
                    score_delta={},
                    transition="FAILED->PASSED",
                ),
            ],
            gate_decision=GateDecision(
                accepted=True,
                reason="全部检查通过",
                checks=[GateCheckResult(check_name="test", passed=True, detail="ok")],
                baseline_pass_rate=0.33,
                candidate_pass_rate=0.66,
                improvement=0.33,
                regressed_case_ids=[],
            ),
            overall_pass_rate_change=0.33,
            overall_verdict="ACCEPTED",
        )
        json_str = report.model_dump_json()
        restored = PipelineReport.model_validate_json(json_str)
        assert restored.pipeline_version == "1.0.0"
        assert restored.overall_verdict == "ACCEPTED"
        assert len(restored.case_deltas) == 1
        assert restored.gate_decision.accepted is True


class TestCriticalCaseConfig:
    """CriticalCaseConfig 模型测试。"""

    def test_basic(self):
        """基本配置只需 eval_id。"""
        cfg = CriticalCaseConfig(eval_id="val_003")
        assert cfg.eval_id == "val_003"
        assert cfg.metric_name is None

    def test_with_metric(self):
        """可指定特定 metric 而非整体状态。"""
        cfg = CriticalCaseConfig(eval_id="val_003", metric_name="final_response_avg_score")
        assert cfg.eval_id == "val_003"
        assert cfg.metric_name == "final_response_avg_score"

    def test_missing_eval_id(self):
        """缺少 eval_id 应触发验证错误。"""
        with pytest.raises(ValidationError):
            CriticalCaseConfig()


class TestFailureAttributionReport:
    """FailureAttributionReport 模型测试。"""

    def test_empty_report(self):
        """空报告应有默认空值。"""
        report = FailureAttributionReport(total_cases_evaluated=0, total_failed=0)
        assert report.clusters == {}
        assert report.per_case_categories == {}
        assert report.summary == ""

    def test_with_failures(self):
        """包含失败数据的报告应正确存储聚类信息。"""
        report = FailureAttributionReport(
            total_cases_evaluated=6,
            total_failed=4,
            clusters={
                "final_response_mismatch": ["train_001", "train_002", "val_001"],
                "tool_trajectory_mismatch": ["val_002"],
            },
            per_case_categories={
                "train_001": ["final_response_mismatch"],
                "train_002": ["final_response_mismatch"],
                "val_001": ["final_response_mismatch"],
                "val_002": ["tool_trajectory_mismatch"],
            },
            summary="4 个 case 失败。主要问题: 回复不匹配 (3 个 case)。",
        )
        assert len(report.clusters) == 2
        assert len(report.clusters["final_response_mismatch"]) == 3
        assert report.total_failed == 4
