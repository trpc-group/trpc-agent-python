"""三类验收场景的端到端测试。

验收标准 #3：必须拒绝"训练集提升但验证集退化"的过拟合候选。
验证 fix_attributed / noop / overfit 三个场景产生三种正确的 gate 决策。
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.attribution import attribute_failures
from pipeline.baseline import run_baseline_fake
from pipeline.config import load_pipeline_config
from pipeline.gate import GateDecision, evaluate_gate
from pipeline.optimize import run_optimize_fake
from pipeline.validate import run_validation_trace


@pytest.fixture
def base_config(data_dir):
    """默认 fake 模式配置。"""
    return load_pipeline_config(
        mode="fake",
        train_evalset=str(data_dir / "train.evalset.json"),
        val_evalset=str(data_dir / "val.evalset.json"),
    )


def _run_pipeline_stages(cfg):
    """跑 baseline → attribution → optimize → validate → gate，返回 gate。"""
    baseline_train = run_baseline_fake(cfg.train_evalset, cfg)
    baseline_val = run_baseline_fake(cfg.val_evalset, cfg)
    attribution = attribute_failures(
        baseline_train.__dict__, baseline_val.__dict__,
    )
    optimize_result = run_optimize_fake(attribution, cfg, scenario=cfg.scenario)
    validation = run_validation_trace(
        cfg.train_evalset, cfg.val_evalset, baseline_val,
        optimize_result, cfg, scenario=cfg.scenario,
        val_regression_cases=cfg.val_regression_cases,
    )
    candidate_train = validation.candidate_train or baseline_train
    gate = evaluate_gate(
        baseline_pass_rate=baseline_train.pass_rate,
        candidate_pass_rate=candidate_train.pass_rate,
        baseline_metrics=baseline_train.metric_breakdown,
        candidate_metrics=candidate_train.metric_breakdown,
        min_improvement=cfg.min_improvement_threshold,
        max_cost=cfg.max_cost_budget,
        optimization_cost=optimize_result.total_cost,
        validation_new_failures=validation.new_failures,
    )
    return gate, validation, baseline_train, candidate_train


class TestFixAttributed:
    def test_gate_accept(self, base_config):
        """fix_attributed 场景：候选修复失败 → 优化成功 → gate ACCEPT。"""
        cfg = base_config
        cfg.scenario = "fix_attributed"
        gate, validation, baseline, candidate = _run_pipeline_stages(cfg)
        assert gate.decision == GateDecision.ACCEPT
        assert candidate.pass_rate > baseline.pass_rate
        assert validation.new_failures == 0


class TestNoop:
    def test_gate_needs_review(self, base_config):
        """noop 场景：候选无实质改动 → 无提升 → gate NEEDS_REVIEW。"""
        cfg = base_config
        cfg.scenario = "noop"
        gate, validation, baseline, candidate = _run_pipeline_stages(cfg)
        assert gate.decision == GateDecision.NEEDS_REVIEW
        assert candidate.pass_rate == pytest.approx(baseline.pass_rate, abs=0.01)


class TestOverfit:
    def test_gate_reject(self, base_config):
        """overfit 场景：train 提升但 val 回归 → gate REJECT（验收标准 #3）。"""
        cfg = base_config
        cfg.scenario = "overfit"
        cfg.val_regression_cases = ["val_simple_math_001", "val_reasoning_001"]
        gate, validation, baseline, candidate = _run_pipeline_stages(cfg)
        assert gate.decision == GateDecision.REJECT
        assert "overfit" in gate.reason.lower() or "validation" in gate.reason.lower()
        assert validation.new_failures > 0
        assert validation.is_overfitting

    def test_overfit_train_improves_val_regresses(self, base_config):
        """过拟合的本质：train pass rate 提升 + val 新增失败。"""
        cfg = base_config
        cfg.scenario = "overfit"
        cfg.val_regression_cases = ["val_simple_math_001", "val_reasoning_001"]
        gate, validation, baseline, candidate = _run_pipeline_stages(cfg)
        # train 提升（记住训练集）
        assert candidate.pass_rate > baseline.pass_rate
        # val 退化（新增失败）
        assert validation.new_failures > 0
        # 对比：baseline val 无失败，候选 val 有失败
        assert validation.candidate is not None
        assert validation.candidate.pass_rate < 1.0


class TestOverfitGate:
    def test_validation_new_failures_rejects(self):
        """gate 直接检查：validation_new_failures > 0 → REJECT。"""
        gate = evaluate_gate(
            baseline_pass_rate=0.7,
            candidate_pass_rate=0.97,
            baseline_metrics={},
            candidate_metrics={},
            min_improvement=0.05,
            max_cost=10.0,
            optimization_cost=0.1,
            validation_new_failures=2,
        )
        assert gate.decision == GateDecision.REJECT
        assert "overfit" in gate.reason.lower()

    def test_no_validation_failures_accepts(self):
        """无 val 回归 + 足够提升 → ACCEPT。"""
        gate = evaluate_gate(
            baseline_pass_rate=0.7,
            candidate_pass_rate=0.97,
            baseline_metrics={},
            candidate_metrics={},
            min_improvement=0.05,
            max_cost=10.0,
            optimization_cost=0.1,
            validation_new_failures=0,
        )
        assert gate.decision == GateDecision.ACCEPT


class TestCandidateConversation:
    def test_candidate_conversation_replayed(self, data_dir):
        """case 携带 candidate_conversation 时按回放评分（非模拟）。"""
        cfg = load_pipeline_config(
            mode="fake",
            train_evalset=str(data_dir / "train.evalset.json"),
            val_evalset=str(data_dir / "val.evalset.json"),
            scenario="fix_attributed",
        )
        baseline_train = run_baseline_fake(cfg.train_evalset, cfg)
        baseline_val = run_baseline_fake(cfg.val_evalset, cfg)
        attribution = attribute_failures(
            baseline_train.__dict__, baseline_val.__dict__,
        )
        optimize_result = run_optimize_fake(attribution, cfg, scenario="fix_attributed")
        validation = run_validation_trace(
            cfg.train_evalset, cfg.val_evalset, baseline_val,
            optimize_result, cfg, scenario="fix_attributed",
        )
        # 候选有 per_case_results（非空）
        assert validation.candidate is not None
        assert len(validation.candidate.per_case_results) > 0
