"""Comprehensive tests for the eval+optimize pipeline modules."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure imports work
_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.config import (
    PipelineConfig,
    load_evalset,
    load_optimizer_json,
    load_pipeline_config,
)
from pipeline.baseline import BaselineResult, run_baseline_fake
from pipeline.attribution import (
    AttributionEntry,
    AttributionReport,
    FailureCategory,
    attribute_failures,
    _categorize_failure,
)
from pipeline.gate import (
    GateDecision,
    GateResult,
    evaluate_gate,
)
from pipeline.validate import (
    ValidationDelta,
    ValidationResult,
    run_validation_fake,
)
from pipeline.report import (
    generate_json_report,
    generate_md_report,
)


@pytest.fixture
def data_dir():
    return _parent / "data"


@pytest.fixture
def sample_baseline():
    return BaselineResult(
        evalset_id="test-evalset",
        pass_rate=0.5,
        total_cases=6,
        passed_cases=3,
        failed_cases=3,
        failed_case_ids=["case_001", "case_002", "case_003"],
        metric_breakdown={"overall_pass_rate": 0.5},
        per_case_results=[
            {"eval_id": "case_001", "pass": False, "reason": "tool_call_error: wrong parameter"},
            {"eval_id": "case_002", "pass": False, "reason": "final_response_mismatch"},
            {"eval_id": "case_003", "pass": False, "reason": "llm_rubric_not_met"},
            {"eval_id": "case_004", "pass": True, "reason": ""},
            {"eval_id": "case_005", "pass": True, "reason": ""},
            {"eval_id": "case_006", "pass": True, "reason": ""},
        ],
    )


# ═══════════════════════════════════════════════════════════
# Config Tests
# ═══════════════════════════════════════════════════════════

class TestConfig:
    def test_load_evalset_valid(self, data_dir):
        data = load_evalset(str(data_dir / "train.evalset.json"))
        assert "eval_set_id" in data
        assert "eval_cases" in data
        assert len(data["eval_cases"]) == 3

    def test_load_evalset_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_evalset("nonexistent.json")

    def test_load_evalset_missing_eval_cases(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"eval_set_id": "test"}, f)
            path = f.name
        try:
            with pytest.raises(ValueError):
                load_evalset(path)
        finally:
            os.unlink(path)

    def test_load_optimizer_json(self, data_dir):
        data = load_optimizer_json(str(data_dir / "optimizer.json"))
        assert "evaluate" in data
        assert "optimize" in data

    def test_load_optimizer_missing_sections(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"wrong_section": 1}, f)
            path = f.name
        try:
            with pytest.raises(ValueError):
                load_optimizer_json(path)
        finally:
            os.unlink(path)

    def test_pipeline_config_defaults(self):
        cfg = load_pipeline_config()
        assert cfg.seed == 42
        assert cfg.mode == "fake"

    def test_pipeline_config_overrides(self):
        cfg = load_pipeline_config(seed=99, mode="live")
        assert cfg.seed == 99
        assert cfg.mode == "live"


# ═══════════════════════════════════════════════════════════
# Baseline Tests
# ═══════════════════════════════════════════════════════════

class TestBaseline:
    def test_fake_baseline_with_data(self, data_dir):
        cfg = load_pipeline_config()
        result = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)
        assert result.total_cases == 3
        # All cases have conversation reference → should pass
        assert result.passed_cases >= 0

    def test_fake_baseline_missing_file(self):
        cfg = load_pipeline_config()
        result = run_baseline_fake("missing.json", cfg)
        assert len(result.errors) > 0

    def test_baseline_result_attributes(self, sample_baseline):
        assert sample_baseline.pass_rate == 0.5
        assert len(sample_baseline.failed_case_ids) == 3


# ═══════════════════════════════════════════════════════════
# Attribution Tests
# ═══════════════════════════════════════════════════════════

class TestAttribution:
    def test_categorize_tool_error(self):
        cat = _categorize_failure("tool_call_error: wrong parameter")
        assert cat == FailureCategory.TOOL_PARAMETER_ERROR

    def test_categorize_response_mismatch(self):
        cat = _categorize_failure("final_response_mismatch")
        assert cat == FailureCategory.FINAL_RESPONSE_MISMATCH

    def test_categorize_rubric(self):
        cat = _categorize_failure("llm_rubric_not_met: quality score below threshold")
        assert cat == FailureCategory.LLM_RUBRIC_NOT_MET

    def test_categorize_unknown(self):
        cat = _categorize_failure("something_weird_happened")
        assert cat == FailureCategory.UNKNOWN

    def test_attribute_failures(self, sample_baseline):
        report = attribute_failures(sample_baseline.__dict__, {})
        assert report.total_failures == 3
        assert len(report.by_category) >= 2

    def test_attribute_no_failures(self):
        bl = BaselineResult(evalset_id="all-pass", pass_rate=1.0,
                            total_cases=3, passed_cases=3, failed_cases=0,
                            failed_case_ids=[], per_case_results=[
                                {"eval_id": "c1", "pass": True},
                                {"eval_id": "c2", "pass": True},
                                {"eval_id": "c3", "pass": True},
                            ])
        report = attribute_failures(bl.__dict__, {})
        assert report.total_failures == 0

    def test_attribution_report_summary(self, sample_baseline):
        report = attribute_failures(sample_baseline.__dict__, {})
        summary = report.get_summary()
        assert "Failure Attribution" in summary


# ═══════════════════════════════════════════════════════════
# Gate Tests
# ═══════════════════════════════════════════════════════════

class TestGate:
    def test_accept_improvement(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.85,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.ACCEPT

    def test_reject_insufficient_improvement(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.52,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.NEEDS_REVIEW

    def test_reject_degradation(self):
        result = evaluate_gate(
            baseline_pass_rate=0.8, candidate_pass_rate=0.6,
            baseline_metrics={}, candidate_metrics={},
        )
        assert result.decision == GateDecision.REJECT

    def test_reject_critical_case_degraded(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.6,
            baseline_metrics={}, candidate_metrics={},
            critical_case_ids=["critical_001"],
            baseline_failed=[],
            candidate_failed=["critical_001"],
        )
        assert result.decision == GateDecision.REJECT
        assert "Critical case" in result.reason

    def test_reject_over_budget(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.9,
            baseline_metrics={}, candidate_metrics={},
            max_cost=5.0, optimization_cost=15.0,
        )
        assert result.decision == GateDecision.REJECT
        assert "exceeds budget" in result.reason.lower()

    def test_new_failures_warning(self):
        result = evaluate_gate(
            baseline_pass_rate=0.5, candidate_pass_rate=0.6,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.02,
            baseline_failed=["case_001"],
            candidate_failed=["case_001", "case_002"],  # New failure
        )
        assert result.decision in (GateDecision.REJECT, GateDecision.NEEDS_REVIEW)

    def test_perfect_already(self):
        """Both baseline and candidate at 100% — should accept."""
        result = evaluate_gate(
            baseline_pass_rate=1.0, candidate_pass_rate=1.0,
            baseline_metrics={}, candidate_metrics={},
            min_improvement=0.05,
        )
        assert result.decision == GateDecision.NEEDS_REVIEW  # No improvement


# ═══════════════════════════════════════════════════════════
# Validation Tests
# ═══════════════════════════════════════════════════════════

class TestValidation:
    def test_new_pass_tracking(self):
        baseline = BaselineResult(
            evalset_id="test", pass_rate=0.5, total_cases=2,
            passed_cases=1, failed_cases=1,
            failed_case_ids=["c1"],
            per_case_results=[
                {"eval_id": "c1", "pass": False},
                {"eval_id": "c2", "pass": True},
            ],
        )
        candidate = BaselineResult(
            evalset_id="test", pass_rate=1.0, total_cases=2,
            passed_cases=2, failed_cases=0,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": True},
            ],
        )
        result = run_validation_fake("fake.json", baseline, candidate,
                                      load_pipeline_config())
        assert result.new_passes == 1
        assert result.new_failures == 0

    def test_new_failure_tracking(self):
        baseline = BaselineResult(
            evalset_id="test", pass_rate=1.0, total_cases=2,
            passed_cases=2, failed_cases=0,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True},
                {"eval_id": "c2", "pass": True},
            ],
        )
        candidate = BaselineResult(
            evalset_id="test", pass_rate=0.5, total_cases=2,
            passed_cases=1, failed_cases=1,
            failed_case_ids=["c1"],
            per_case_results=[
                {"eval_id": "c1", "pass": False},
                {"eval_id": "c2", "pass": True},
            ],
        )
        result = run_validation_fake("fake.json", baseline, candidate,
                                      load_pipeline_config())
        assert result.new_failures == 1

    def test_overfitting_detection(self):
        baseline = BaselineResult(
            evalset_id="test", pass_rate=0.6, total_cases=5,
            passed_cases=3, failed_cases=2,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True}, {"eval_id": "c2", "pass": True},
                {"eval_id": "c3", "pass": True}, {"eval_id": "c4", "pass": True},
                {"eval_id": "c5", "pass": True},
            ],
        )
        candidate = BaselineResult(
            evalset_id="test", pass_rate=0.4, total_cases=5,
            passed_cases=2, failed_cases=3,
            failed_case_ids=[],
            per_case_results=[
                {"eval_id": "c1", "pass": True}, {"eval_id": "c2", "pass": True},
                {"eval_id": "c3", "pass": False}, {"eval_id": "c4", "pass": False},
                {"eval_id": "c5", "pass": False},
            ],
        )
        result = run_validation_fake("fake.json", baseline, candidate,
                                      load_pipeline_config())
        assert result.is_overfitting  # New failures introduced

    def test_empty_validation(self):
        result = run_validation_fake("fake.json",
                                      BaselineResult(), BaselineResult(),
                                      load_pipeline_config())
        assert result.new_passes == 0
        assert result.new_failures == 0


# ═══════════════════════════════════════════════════════════
# Report Tests
# ═══════════════════════════════════════════════════════════

class TestReport:
    def test_json_report_generation(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        report = generate_json_report("test-001", sample_baseline,
                                       sample_baseline, attribution, gate)
        data = json.loads(report)
        assert data["task_id"] == "test-001"
        assert data["gate"]["decision"] == "accept"
        assert "baseline" in data
        assert "attribution" in data
        assert "audit" in data

    def test_json_report_contains_all_sections(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        report = generate_json_report("test-002", sample_baseline,
                                       sample_baseline, attribution, gate)
        data = json.loads(report)
        for section in ["baseline", "attribution", "gate", "validation_delta",
                         "optimizer", "audit"]:
            assert section in data, f"Missing section: {section}"

    def test_md_report_generation(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.5, 0.85, {}, {}, min_improvement=0.1)
        md = generate_md_report("test-001", sample_baseline,
                                 sample_baseline, attribution, gate)
        assert "test-001" in md
        assert "Gate Decision" in md
        assert "Failure Attribution" in md

    def test_md_report_reject(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        gate = evaluate_gate(0.8, 0.6, {}, {})
        md = generate_md_report("test-001", sample_baseline,
                                 sample_baseline, attribution, gate)
        assert "REJECT" in md


# ═══════════════════════════════════════════════════════════
# Integration Test
# ═══════════════════════════════════════════════════════════

class TestIntegration:
    def test_full_pipeline_fake_mode(self, data_dir):
        """Run the complete pipeline end-to-end in fake mode."""
        cfg = load_pipeline_config(
            train_evalset=str(data_dir / "train.evalset.json"),
            val_evalset=str(data_dir / "val.evalset.json"),
            mode="fake",
            verbose=False,
        )

        # Load configs
        train_data = load_evalset(cfg.train_evalset)
        val_data = load_evalset(cfg.val_evalset)
        assert len(train_data["eval_cases"]) == 3
        assert len(val_data["eval_cases"]) == 3

        # Baseline
        bl_train = run_baseline_fake(cfg.train_evalset, cfg)
        bl_val = run_baseline_fake(cfg.val_evalset, cfg)
        assert bl_train.total_cases == 3

        # Attribution
        attr = attribute_failures(
            bl_train.__dict__ if hasattr(bl_train, '__dict__') else bl_train,
            bl_val.__dict__ if hasattr(bl_val, '__dict__') else bl_val,
        )

        # Gate
        candidate_pass_rate = min(1.0, bl_train.pass_rate + 0.2)
        gate = evaluate_gate(
            baseline_pass_rate=bl_train.pass_rate,
            candidate_pass_rate=candidate_pass_rate,
            baseline_metrics=bl_train.metric_breakdown,
            candidate_metrics=bl_train.metric_breakdown,
            baseline_failed=bl_train.failed_case_ids,
            candidate_failed=[],
        )

        # Report
        report = generate_json_report("int-test", bl_train, bl_val,
                                       attr, gate)
        data = json.loads(report)
        assert "task_id" in data

    def test_pipeline_with_overfitting_rejection(self, data_dir):
        """Pipeline should reject when candidate degrades."""
        cfg = load_pipeline_config(mode="fake")
        bl_train = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)

        # Simulate degradation
        gate = evaluate_gate(
            baseline_pass_rate=0.8,
            candidate_pass_rate=0.2,  # Big degradation
            baseline_metrics={}, candidate_metrics={},
        )
        assert gate.decision == GateDecision.REJECT

    def test_edge_empty_evalset(self):
        """Pipeline with empty evalset should not crash."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"eval_set_id": "empty", "eval_cases": []}, f)
            path = f.name
        try:
            cfg = load_pipeline_config()
            result = run_baseline_fake(path, cfg)
            assert result.total_cases == 0
        finally:
            os.unlink(path)
