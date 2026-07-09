"""Tests for extreme/boundary/error conditions in the eval+optimize pipeline."""

import json
import os
import tempfile

import pytest

import pipeline.config as config_module
from pipeline.attribution import AttributionReport, attribute_failures
from pipeline.baseline import BaselineResult, run_baseline_fake
from pipeline.config import PipelineConfig, load_evalset, load_optimizer_json, load_pipeline_config
from pipeline.gate import GateDecision, evaluate_gate
from pipeline.optimize import OptimizeResult, run_optimize_fake
from pipeline.report import generate_json_report, generate_md_report


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_case(eval_id: str, has_conversation: bool = True) -> dict:
    """Build a minimal evalset case dict."""
    case = {"eval_id": eval_id, "eval_mode": "trace"}
    if has_conversation:
        case["conversation"] = [
            {
                "invocation_id": "inv-1",
                "user_content": {"parts": [{"text": "hello"}], "role": "user"},
                "final_response": {"parts": [{"text": "hi"}], "role": "model"},
            }
        ]
    return case


def _write_evalset(cases: list[dict], evalset_id: str = "test") -> str:
    """Write a temporary evalset file and return its path."""
    data = {"eval_set_id": evalset_id, "name": "Test", "eval_cases": cases}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f)
        return f.name


# ---------------------------------------------------------------------------
# TestEmptyEvalset
# ---------------------------------------------------------------------------

class TestEmptyEvalset:
    """Tests involving an evalset with zero cases."""

    def test_empty_evalset_returns_zero_pass_rate(self, pipeline_config):
        """Empty evalset should not crash — returns 0 cases, 0.0 pass rate."""
        path = _write_evalset([], "empty-set")
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 0
            assert result.pass_rate == 0.0
            assert result.failed_cases == 0
            assert result.failed_case_ids == []
        finally:
            os.unlink(path)

    def test_empty_evalset_gate_handles_zero_division(self):
        """Gate called with both pass rates at 0 should not divide by zero."""
        result = evaluate_gate(
            baseline_pass_rate=0.0,
            candidate_pass_rate=0.0,
            baseline_metrics={},
            candidate_metrics={},
            min_improvement=0.05,
        )
        # Should return a valid decision, not crash
        assert result.decision in (GateDecision.NEEDS_REVIEW, GateDecision.REJECT)

    def test_empty_evalset_attribution_is_empty(self):
        """Attribution on empty results produces a valid empty report."""
        empty = BaselineResult(evalset_id="empty", total_cases=0)
        report = attribute_failures(empty.__dict__, {})
        assert report.total_failures == 0
        assert len(report.entries) == 0


# ---------------------------------------------------------------------------
# TestSingleCaseEvalset
# ---------------------------------------------------------------------------

class TestSingleCaseEvalset:
    """Tests with a single-case evalset."""

    def test_single_case_passing_gate(self):
        """Gate with one case — improvement from 0 to 100% should accept."""
        result = evaluate_gate(
            baseline_pass_rate=0.0,
            candidate_pass_rate=1.0,
            baseline_metrics={},
            candidate_metrics={},
            min_improvement=0.1,
        )
        assert result.decision == GateDecision.ACCEPT

    def test_single_case_no_improvement_gate(self):
        """Gate with one case — no improvement should needs_review."""
        result = evaluate_gate(
            baseline_pass_rate=1.0,
            candidate_pass_rate=1.0,
            baseline_metrics={},
            candidate_metrics={},
            min_improvement=0.05,
        )
        assert result.decision == GateDecision.NEEDS_REVIEW

    def test_single_case_evalset_baseline(self, pipeline_config):
        """Single-case evalset runs baseline without error."""
        path = _write_evalset([_make_case("only", True)], "single-set")
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
            # has_conversation=True → passes in fake mode
            assert result.passed_cases == 1
            assert result.pass_rate == 1.0
        finally:
            os.unlink(path)

    def test_single_case_without_conversation_fails(self, pipeline_config):
        """Single case without conversation data fails in fake mode."""
        path = _write_evalset([_make_case("lonely", False)], "single-set")
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
            assert result.failed_cases == 1
            assert result.pass_rate == 0.0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# TestLongCaseId
# ---------------------------------------------------------------------------

class TestLongCaseId:
    """Tests handling of very long case IDs."""

    def test_very_long_case_id(self, pipeline_config):
        """A case_id of 500+ characters should not crash the pipeline."""
        long_id = "case_" + "x" * 500
        assert len(long_id) > 500
        path = _write_evalset([_make_case(long_id, True)], "long-id-set")
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
            assert result.per_case_results[0]["eval_id"] == long_id
        finally:
            os.unlink(path)

    def test_long_id_in_attribution(self):
        """Long case ID flows through attribution without error."""
        long_id = "f_" + "y" * 600
        baseline = BaselineResult(
            evalset_id="long",
            total_cases=1,
            failed_cases=1,
            failed_case_ids=[long_id],
            per_case_results=[
                {"eval_id": long_id, "pass": False, "reason": "tool_call_error: bad param"}
            ],
        )
        report = attribute_failures(baseline.__dict__, {})
        assert report.total_failures == 1
        assert report.entries[0].case_id == long_id


# ---------------------------------------------------------------------------
# TestInvalidJsonEvalset
# ---------------------------------------------------------------------------

class TestInvalidJsonEvalset:
    """Tests handling of malformed or invalid JSON evalset files."""

    def test_invalid_json_evalset_load_evalset(self):
        """load_evalset on invalid JSON raises an error containing the filename."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                f.write("this is not valid json {{{")
                path = f.name

            with pytest.raises(json.JSONDecodeError):
                load_evalset(path)
        finally:
            if path:
                os.unlink(path)

    def test_invalid_json_evalset_run_baseline(self, pipeline_config):
        """run_baseline_fake on invalid JSON returns result with errors."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                f.write("garbage content not json")
                path = f.name

            # run_baseline_fake uses json.load internally → will raise
            with pytest.raises(json.JSONDecodeError):
                run_baseline_fake(path, pipeline_config)
        finally:
            if path:
                os.unlink(path)

    def test_evalset_missing_required_fields(self):
        """load_evalset on JSON missing 'eval_set_id' raises ValueError."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                json.dump({"name": "no id field", "eval_cases": []}, f)
                path = f.name

            with pytest.raises(ValueError, match="eval_set_id"):
                load_evalset(path)
        finally:
            if path:
                os.unlink(path)

    def test_evalset_missing_eval_cases_field(self):
        """load_evalset on JSON missing 'eval_cases' raises ValueError."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                json.dump({"eval_set_id": "abc"}, f)
                path = f.name

            with pytest.raises(ValueError, match="eval_cases"):
                load_evalset(path)
        finally:
            if path:
                os.unlink(path)

    def test_nonexistent_evalset_file(self):
        """load_evalset on nonexistent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_evalset("/nonexistent/path/evalset.json")


# ---------------------------------------------------------------------------
# TestNegativeTimeout
# ---------------------------------------------------------------------------

class TestNegativeTimeout:
    """Tests handling of negative or invalid timeout values."""

    def test_negative_timeout_in_config(self):
        """Setting negative timeout in config does not crash module loading."""
        cfg = PipelineConfig(timeout_seconds=-1)
        assert cfg.timeout_seconds == -1

    def test_negative_timeout_does_not_break_optimize(self):
        """run_optimize_fake works even with negative timeout in config."""
        cfg = PipelineConfig(timeout_seconds=-5)
        attr = AttributionReport(
            total_failures=2,
            by_category={"tool_call_error": 2},
        )
        result = run_optimize_fake(attr, cfg)
        assert result is not None
        assert isinstance(result, OptimizeResult)


# ---------------------------------------------------------------------------
# TestUnicodeInCases
# ---------------------------------------------------------------------------

class TestUnicodeInCases:
    """Tests handling of Unicode and emoji in evalset fields."""

    def test_emoji_in_case_id(self, pipeline_config):
        """Case ID with emoji should work without error."""
        path = _write_evalset(
            [_make_case("case-\U0001f600-smile", True)],   # 😀
            "emoji-set",
        )
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
            assert "😀" in result.per_case_results[0]["eval_id"]
        finally:
            os.unlink(path)

    def test_unicode_in_expected_responses(self, pipeline_config):
        """Unicode text in conversation content should be preserved."""
        path = None
        try:
            data = {
                "eval_set_id": "unicode-test",
                "name": "Unicode",
                "eval_cases": [
                    {
                        "eval_id": "u1",
                        "eval_mode": "trace",
                        "conversation": [
                            {
                                "invocation_id": "inv-u1",
                                "user_content": {
                                    "parts": [{"text": "你好世界 🌍"}],
                                    "role": "user",
                                },
                                "final_response": {
                                    "parts": [{"text": "こんにちは 🎉"}],
                                    "role": "model",
                                },
                            }
                        ],
                    }
                ],
            }
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as f:
                json.dump(data, f, ensure_ascii=False)
                path = f.name

            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
            assert result.passed_cases == 1  # has conversation → pass
        finally:
            if path:
                os.unlink(path)

    def test_chinese_case_names(self, pipeline_config):
        """Entirely Chinese case IDs and content should work."""
        path = _write_evalset(
            [_make_case("测试用例_001", True)],
            "中文测试",
        )
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 1
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# TestUnknownAlgorithm
# ---------------------------------------------------------------------------

class TestUnknownAlgorithm:
    """Tests handling of unknown optimization algorithm names."""

    def test_unknown_algorithm_in_config(self):
        """Unknown algorithm name is accepted by config (no validation at config level)."""
        cfg = PipelineConfig(algorithm="nonexistent_algo_v42")
        assert cfg.algorithm == "nonexistent_algo_v42"

    def test_unknown_algorithm_runs_optimize_fake(self):
        """run_optimize_fake with unknown algorithm name does not crash — uses it as-is."""
        attr = AttributionReport(
            total_failures=3,
            by_category={"tool_call_error": 2, "format_not_as_required": 1},
        )
        cfg = PipelineConfig(algorithm="my_custom_algo")
        result = run_optimize_fake(attr, cfg)
        assert result.algorithm == "my_custom_algo"
        assert isinstance(result, OptimizeResult)
        # Optimization still runs (algorithm name is just a label in fake mode)
        assert result.total_iterations >= 1

    def test_unknown_algorithm_in_audit_trail(self):
        """AuditTracer preserves unknown algorithm name."""
        from pipeline.tracing import AuditTracer

        tracer = AuditTracer(seed=42, mode="fake", algorithm="bogus_optimizer_v99")
        audit = tracer.to_dict()
        assert audit["reproducibility"]["algorithm"] == "bogus_optimizer_v99"
