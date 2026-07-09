"""Tests for baseline evaluation module."""

import os

import pytest

from pipeline.baseline import BaselineResult, run_baseline_fake, run_baseline_sdk
from pipeline.config import load_pipeline_config


class TestBaselineResult:
    """Tests for the BaselineResult dataclass."""

    def test_default_values(self):
        br = BaselineResult()
        assert br.pass_rate == 0.0
        assert br.total_cases == 0
        assert br.failed_case_ids == []

    def test_pass_rate_calculation(self):
        br = BaselineResult(total_cases=10, passed_cases=7)
        assert br.pass_rate == 0.0  # Not auto-calculated, uses field default

    def test_errors_field(self):
        br = BaselineResult(errors=["error1", "error2"])
        assert len(br.errors) == 2

    def test_metric_breakdown(self):
        br = BaselineResult(metric_breakdown={
            "response_match_score": 0.8,
            "tool_trajectory_avg_score": 0.6,
        })
        assert len(br.metric_breakdown) == 2


class TestRunBaselineFake:
    """Tests for fake baseline evaluation."""

    def test_with_valid_data(self, data_dir, pipeline_config):
        result = run_baseline_fake(
            str(data_dir / "train.evalset.json"), pipeline_config,
        )
        assert result.total_cases >= 3  # Expanded evalset
        assert "train" in result.evalset_id.lower()

    def test_missing_file(self, pipeline_config):
        result = run_baseline_fake("missing.json", pipeline_config)
        assert len(result.errors) > 0
        assert "not found" in result.errors[0].lower()

    def test_empty_evalset(self, pipeline_config, temp_json_file):
        path = temp_json_file({"eval_set_id": "empty", "eval_cases": []})
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 0
            assert result.pass_rate == 0.0
        finally:
            os.unlink(path)

    def test_all_cases_with_conversation_pass(self, pipeline_config, temp_json_file):
        path = temp_json_file({
            "eval_set_id": "test",
            "eval_cases": [
                {"eval_id": "c1", "conversation": [{"text": "hello"}]},
                {"eval_id": "c2", "conversation": [{"text": "world"}]},
            ],
        })
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 2
            assert result.passed_cases == 2
        finally:
            os.unlink(path)

    def test_failed_case_ids_tracked(self, pipeline_config, temp_json_file):
        path = temp_json_file({
            "eval_set_id": "test",
            "eval_cases": [
                {"eval_id": "pass_case", "conversation": [{"text": "data"}]},
                {"eval_id": "fail_case"},
            ],
        })
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert "fail_case" in result.failed_case_ids
            assert "pass_case" not in result.failed_case_ids
        finally:
            os.unlink(path)


class TestRunBaselineSdk:
    """Tests for SDK baseline path."""

    def test_sdk_stub_returns_result(self):
        result = run_baseline_sdk("some/path.json")
        assert isinstance(result, BaselineResult)
        # SDK not available in test environment → error recorded
        assert len(result.errors) > 0
