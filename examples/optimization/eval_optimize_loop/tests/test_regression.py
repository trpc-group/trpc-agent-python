"""Tests for reproducibility and non-regression in the eval+optimize pipeline."""

import json
import os
import tempfile

import pytest

from pipeline.attribution import AttributionReport, attribute_failures
from pipeline.baseline import BaselineResult, run_baseline_fake
from pipeline.config import PipelineConfig, load_pipeline_config
from pipeline.gate import GateDecision, GateResult, evaluate_gate
from pipeline.optimize import OptimizeResult, run_optimize_fake
from pipeline.report import generate_json_report, generate_md_report
from pipeline.validate import ValidationResult


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_evalset(cases: list[dict], evalset_id: str = "regression-set") -> str:
    """Write a temporary evalset and return path."""
    data = {"eval_set_id": evalset_id, "name": "Test", "eval_cases": cases}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f)
        return f.name


def _make_case(eval_id: str, has_conversation: bool = True) -> dict:
    """Build a minimal evalset case dict."""
    case = {"eval_id": eval_id, "eval_mode": "trace"}
    if has_conversation:
        case["conversation"] = [
            {
                "invocation_id": "inv-1",
                "user_content": {"parts": [{"text": "q"}], "role": "user"},
                "final_response": {"parts": [{"text": "a"}], "role": "model"},
            }
        ]
    return case


def _attribution_with_failures(count: int) -> AttributionReport:
    """Build an AttributionReport with `count` failures across two categories."""
    return AttributionReport(
        total_failures=count,
        by_category={"tool_call_error": count // 2 + count % 2,
                      "final_response_mismatch": count // 2},
    )


# ---------------------------------------------------------------------------
# TestSeedReproducibility
# ---------------------------------------------------------------------------

class TestSeedReproducibility:
    """Tests that pipeline results are reproducible given the same seed."""

    def test_same_seed_same_baseline_results(self, pipeline_config):
        """Two baseline runs on the same evalset produce identical pass_rate."""
        cases = [
            _make_case("c1", True),
            _make_case("c2", True),
            _make_case("c3", False),
            _make_case("c4", True),
            _make_case("c5", False),
            _make_case("c6", True),
        ]
        path = _make_evalset(cases)
        try:
            cfg1 = load_pipeline_config(mode="fake", verbose=False)
            cfg2 = load_pipeline_config(mode="fake", verbose=False)
            r1 = run_baseline_fake(path, cfg1)
            r2 = run_baseline_fake(path, cfg2)
            assert r1.pass_rate == r2.pass_rate
            assert r1.total_cases == r2.total_cases
            assert r1.passed_cases == r2.passed_cases
            assert r1.failed_cases == r2.failed_cases
            assert r1.failed_case_ids == r2.failed_case_ids
        finally:
            os.unlink(path)

    def test_same_seed_same_optimize_results(self):
        """Two optimize runs with the same attribution produce identical round results."""
        cfg1 = PipelineConfig(seed=42, algorithm="gepa_reflective", max_iterations=3)
        cfg2 = PipelineConfig(seed=42, algorithm="gepa_reflective", max_iterations=3)

        attr1 = _attribution_with_failures(6)
        attr2 = _attribution_with_failures(6)

        result1 = run_optimize_fake(attr1, cfg1)
        result2 = run_optimize_fake(attr2, cfg2)

        assert result1.total_iterations == result2.total_iterations
        assert result1.total_cost == result2.total_cost
        assert result1.converged == result2.converged
        assert len(result1.rounds) == len(result2.rounds)
        for r1, r2 in zip(result1.rounds, result2.rounds):
            # Deterministic fake mode: round scores should match exactly
            assert r1.score == pytest.approx(r2.score)
            assert r1.round_index == r2.round_index

    def test_same_seed_attribution_consistent(self, sample_baseline):
        """Attribution on the same baseline data is deterministic (no randomness)."""
        r1 = attribute_failures(sample_baseline.__dict__, {})
        r2 = attribute_failures(sample_baseline.__dict__, {})
        assert r1.total_failures == r2.total_failures
        assert r1.by_category == r2.by_category
        assert [e.case_id for e in r1.entries] == [e.case_id for e in r2.entries]
        assert [e.category for e in r1.entries] == [e.category for e in r2.entries]


# ---------------------------------------------------------------------------
# TestAllPassBaselineNoModification
# ---------------------------------------------------------------------------

class TestAllPassBaselineNoModification:
    """When baseline already passes everything, optimization should not modify anything."""

    def test_all_pass_optimize_no_rounds(self):
        """With zero failures, optimization has no rounds and source is untouched."""
        attr = AttributionReport(total_failures=0, by_category={})
        cfg = PipelineConfig(seed=42, max_iterations=3)
        result = run_optimize_fake(attr, cfg)

        assert result.converged is True
        assert result.total_iterations == 0
        assert len(result.rounds) == 0
        # best_prompt is empty since nothing to optimize
        assert result.best_prompt == {}
        assert result.optimized_fields == []

    def test_all_pass_baseline_attribution_empty(self, all_pass_baseline):
        """Attribute failures on all-pass baseline returns 0 failures."""
        report = attribute_failures(all_pass_baseline.__dict__, {})
        assert report.total_failures == 0
        assert len(report.entries) == 0
        assert len(report.by_category) == 0

    def test_all_pass_no_optimized_fields(self):
        """When there are no failures, no prompt fields are marked as optimized."""
        attr = AttributionReport(total_failures=0, by_category={})
        cfg = PipelineConfig(seed=42, max_iterations=5)
        result = run_optimize_fake(attr, cfg)
        assert result.optimized_fields == []
        assert not result.best_prompt


# ---------------------------------------------------------------------------
# TestGateRejectReportGeneration
# ---------------------------------------------------------------------------

class TestGateRejectReportGeneration:
    """A gate REJECT decision must not crash report generation."""

    def test_reject_gate_json_report_generated(self, sample_baseline, all_pass_baseline):
        """JSON report for a rejected gate is valid JSON and contains the decision."""
        reject = GateResult(
            decision=GateDecision.REJECT,
            reason="Candidate degraded by 0.25 — rejecting",
            details={
                "improvement": -0.25,
                "checks": [
                    {"check": "improvement_threshold", "passed": False,
                     "detail": "Improvement: -25% (threshold: 5%)"},
                ],
            },
        )
        attr = attribute_failures(sample_baseline.__dict__, {})
        report_str = generate_json_report(
            task_id="regression-reject-001",
            baseline_train=sample_baseline,
            baseline_val=all_pass_baseline,
            attribution=attr,
            gate=reject,
        )
        report = json.loads(report_str)
        assert report["gate"]["decision"] == "reject"
        assert "rejecting" in report["gate"]["reason"].lower()
        assert report["task_id"] == "regression-reject-001"

    def test_reject_gate_md_report_generated(self, sample_baseline, all_pass_baseline):
        """Markdown report for a rejected gate contains the reject indicator."""
        reject = GateResult(
            decision=GateDecision.REJECT,
            reason="Cost $15.00 exceeds budget $10.00",
            details={
                "cost": 15.0,
                "checks": [
                    {"check": "cost_budget", "passed": False,
                     "detail": "Cost: $15.00 / $10.00"},
                ],
            },
        )
        attr = attribute_failures(sample_baseline.__dict__, {})
        md = generate_md_report(
            task_id="reject-md-002",
            baseline_train=sample_baseline,
            baseline_val=all_pass_baseline,
            attribution=attr,
            gate=reject,
        )
        assert "REJECT" in md
        assert "reject-md-002" in md
        # Should still include attribution section
        assert "Failure Attribution" in md

    def test_needs_review_gate_json_report(self, sample_baseline, all_pass_baseline):
        """JSON report for needs_review is also valid."""
        review = GateResult(
            decision=GateDecision.NEEDS_REVIEW,
            reason="Improvement +2% below threshold 5%",
            details={
                "improvement": 0.02,
                "checks": [
                    {"check": "improvement_threshold", "passed": False,
                     "detail": "Improvement: +2% (threshold: 5%)"},
                ],
            },
        )
        attr = attribute_failures(sample_baseline.__dict__, {})
        report_str = generate_json_report(
            task_id="review-003",
            baseline_train=sample_baseline,
            baseline_val=all_pass_baseline,
            attribution=attr,
            gate=review,
        )
        report = json.loads(report_str)
        assert report["gate"]["decision"] == "needs_review"

    def test_reject_report_with_validation_delta(self, sample_baseline, all_pass_baseline):
        """Report with REJECT gate + validation delta is still valid."""
        from pipeline.validate import ValidationDelta

        reject = GateResult(
            decision=GateDecision.REJECT,
            reason="Critical case regressed",
            details={"critical_regressed": ["crit-1"], "checks": []},
        )
        attr = attribute_failures(sample_baseline.__dict__, {})
        validation = ValidationResult(
            baseline=sample_baseline,
            candidate=all_pass_baseline,
            deltas=[
                ValidationDelta(eval_id="c1", baseline_passed=False,
                                candidate_passed=True, change="new_pass"),
                ValidationDelta(eval_id="c2", baseline_passed=True,
                                candidate_passed=False, change="new_fail"),
            ],
        )
        report_str = generate_json_report(
            task_id="reject-with-val-004",
            baseline_train=sample_baseline,
            baseline_val=all_pass_baseline,
            attribution=attr,
            gate=reject,
            validation=validation,
        )
        report = json.loads(report_str)
        assert report["gate"]["decision"] == "reject"
        assert report["validation_delta"]["new_passes"] == 1
        assert report["validation_delta"]["new_failures"] == 1


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Pipeline handles older/alternate evalset formats gracefully."""

    def test_minimal_evalset_format(self, pipeline_config):
        """Evalset with only eval_set_id and eval_cases works (no 'name' field)."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                json.dump({
                    "eval_set_id": "minimal-set",
                    "eval_cases": [
                        {"eval_id": "m1", "eval_mode": "trace",
                         "conversation": [{"invocation_id": "i1",
                                           "user_content": {"parts": [{"text": "?"}], "role": "user"},
                                           "final_response": {"parts": [{"text": "!"}], "role": "model"}}]}
                    ],
                }, f)
                path = f.name

            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
            assert result.evalset_id == "minimal-set"
        finally:
            if path:
                os.unlink(path)

    def test_evalset_with_extra_unknown_fields(self, pipeline_config):
        """Evalset with extra unknown top-level fields is handled gracefully."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                json.dump({
                    "eval_set_id": "extra-fields",
                    "eval_cases": [
                        {"eval_id": "e1", "eval_mode": "trace",
                         "conversation": [{"invocation_id": "i1",
                                           "user_content": {"parts": [{"text": "x"}], "role": "user"},
                                           "final_response": {"parts": [{"text": "y"}], "role": "model"}}]}
                    ],
                    # Older fields that newer code should ignore
                    "version": "1.0",
                    "schema_version": 2,
                    "legacy_field": "should be ignored",
                }, f)
                path = f.name

            # Should not crash; extra fields are ignored
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
            assert result.evalset_id == "extra-fields"
        finally:
            if path:
                os.unlink(path)

    def test_evalset_case_with_extra_fields(self, pipeline_config):
        """Case dicts with extra fields beyond eval_id are handled gracefully."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                json.dump({
                    "eval_set_id": "extra-case-fields",
                    "eval_cases": [
                        {
                            "eval_id": "rich-1",
                            "eval_mode": "trace",
                            "description": "This field is extra",
                            "tags": ["math", "addition"],
                            "priority": "high",
                            "conversation": [
                                {"invocation_id": "i1",
                                 "user_content": {"parts": [{"text": "q"}], "role": "user"},
                                 "final_response": {"parts": [{"text": "a"}], "role": "model"}}
                            ],
                        }
                    ],
                }, f)
                path = f.name

            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
        finally:
            if path:
                os.unlink(path)

    def test_optimizer_json_with_only_required_sections(self, temp_json_file):
        """Optimizer config with only 'evaluate' and 'optimize' sections loads."""
        from pipeline.config import load_optimizer_json

        path = temp_json_file({
            "evaluate": {"metrics": [{"metric_name": "pass", "threshold": 0.5}]},
            "optimize": {"algorithm": {"name": "gepa_reflective"}},
        })
        try:
            data = load_optimizer_json(path)
            assert "evaluate" in data
            assert "optimize" in data
        finally:
            os.unlink(path)

    def test_optimizer_json_missing_evaluate_section(self, temp_json_file):
        """Optimizer config missing 'evaluate' section raises ValueError."""
        from pipeline.config import load_optimizer_json

        path = temp_json_file({
            "optimize": {"algorithm": {"name": "test"}},
        })
        try:
            with pytest.raises(ValueError, match="missing 'evaluate'"):
                load_optimizer_json(path)
        finally:
            os.unlink(path)
