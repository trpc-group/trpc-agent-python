"""Tests for optimization module (optimize.py)."""

import pytest

from pipeline.optimize import (
    OptimizeResult,
    RoundRecord,
    run_optimize_fake,
    _simulate_prompt_change,
    _build_optimized_prompt,
)
from pipeline.attribution import attribute_failures
from pipeline.config import load_pipeline_config


class TestRoundRecord:
    """Tests for RoundRecord dataclass."""

    def test_default_values(self):
        record = RoundRecord(round_index=1, score=0.5, best_so_far=0.5)
        assert record.round_index == 1
        assert record.cost == 0.0
        assert record.prompt_changes == []

    def test_with_changes(self):
        record = RoundRecord(
            round_index=2, score=0.8, best_so_far=0.8,
            prompt_changes=["Improved tool handling"],
            cost=0.05,
            duration_ms=123.4,
        )
        assert len(record.prompt_changes) == 1
        assert record.cost == 0.05
        assert record.duration_ms == 123.4


class TestOptimizeResult:
    """Tests for OptimizeResult dataclass."""

    def test_default_values(self):
        result = OptimizeResult()
        assert result.algorithm == "gepa_reflective"
        assert result.best_score == 0.0
        assert result.converged is False

    def test_best_score_with_rounds(self):
        result = OptimizeResult(rounds=[
            RoundRecord(1, 0.5, 0.5),
            RoundRecord(2, 0.7, 0.7),
            RoundRecord(3, 0.6, 0.7),
        ])
        assert result.best_score == 0.7

    def test_best_score_empty(self):
        result = OptimizeResult()
        assert result.best_score == 0.0


class TestSimulatePromptChange:
    """Tests for _simulate_prompt_change()."""

    def test_known_category(self):
        change = _simulate_prompt_change("tool_call_error")
        assert "tool" in change.lower()
        assert len(change) > 0

    def test_unknown_category(self):
        change = _simulate_prompt_change("bizarre_new_category")
        assert "bizarre_new_category" in change
        assert "improved handling" in change

    def test_all_categories_produce_output(self):
        categories = [
            "final_response_mismatch", "tool_call_error",
            "wrong_tool_selected", "tool_parameter_error",
            "llm_rubric_not_met", "knowledge_recall_insufficient",
            "format_not_as_required", "missing_expected_output",
            "unknown",
        ]
        for cat in categories:
            change = _simulate_prompt_change(cat)
            assert len(change) > 10, f"Category '{cat}' produced short output"


class TestBuildOptimizedPrompt:
    """Tests for _build_optimized_prompt()."""

    def test_single_change(self):
        prompt = _build_optimized_prompt({
            "tool_call_error": "Fix: validate tool params.",
        })
        assert "Optimized System Prompt" in prompt
        assert "tool_call_error" in prompt
        assert "Original Baseline" in prompt

    def test_multiple_changes(self):
        prompt = _build_optimized_prompt({
            "tool_call_error": "Fix A",
            "format_not_as_required": "Fix B",
        })
        assert "Fix A" in prompt
        assert "Fix B" in prompt


class TestRunOptimizeFake:
    """Tests for run_optimize_fake()."""

    def test_with_failures(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        config = load_pipeline_config(max_iterations=3)
        result = run_optimize_fake(attribution, config)
        assert result.total_iterations > 0
        assert result.total_cost > 0
        assert len(result.rounds) > 0

    def test_no_failures(self, all_pass_baseline):
        attribution = attribute_failures(all_pass_baseline.__dict__, {})
        config = load_pipeline_config()
        result = run_optimize_fake(attribution, config)
        assert result.converged is True
        assert result.total_iterations == 0
        assert result.total_cost == 0.0

    def test_respects_max_iterations(self, all_fail_baseline):
        attribution = attribute_failures(all_fail_baseline.__dict__, {})
        config = load_pipeline_config(max_iterations=2)
        result = run_optimize_fake(attribution, config)
        assert result.total_iterations <= 2

    def test_optimized_fields_present(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        config = load_pipeline_config()
        result = run_optimize_fake(attribution, config)
        assert "system.md" in result.optimized_fields

    def test_best_prompt_not_empty(self, sample_baseline):
        attribution = attribute_failures(sample_baseline.__dict__, {})
        config = load_pipeline_config()
        result = run_optimize_fake(attribution, config)
        assert result.best_prompt
        assert "system.md" in result.best_prompt

    def test_rounds_have_increasing_scores(self, all_fail_baseline):
        attribution = attribute_failures(all_fail_baseline.__dict__, {})
        config = load_pipeline_config(max_iterations=4)
        result = run_optimize_fake(attribution, config)
        scores = [r.score for r in result.rounds]
        # Scores should be non-decreasing (each round fixes more failures)
        for i in range(1, len(scores)):
            assert scores[i] >= scores[i - 1], f"Score decreased at round {i}"
