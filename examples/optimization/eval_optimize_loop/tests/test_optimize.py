"""Tests for optimization module (optimize.py)."""

import pytest

from pipeline.optimize import (
    OptimizeResult,
    RoundRecord,
    run_optimize_fake,
    _anchor_to_example_dir,
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
        # 口径字段默认空；fake/live 各自在入口设置（reviewer Warning：外部消费
        # best_score 时必须同时读 best_score_metric，避免 fake/live 误比）
        assert result.best_score_metric == ""

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

    def test_converged_when_all_categories_fixed(self, sample_baseline):
        # 3 个失败类别、max_iterations=3 恰好全修 → 视为收敛
        attribution = attribute_failures(sample_baseline.__dict__, {})
        assert len(attribution.by_category) == 3
        result = run_optimize_fake(attribution, load_pipeline_config(max_iterations=3))
        assert result.total_iterations == 3
        assert result.converged is True

    def test_not_converged_when_capped_by_iterations(self, sample_baseline):
        # max_iterations=1 只修了 3 类中的 1 类 → 未收敛
        attribution = attribute_failures(sample_baseline.__dict__, {})
        assert len(attribution.by_category) == 3
        result = run_optimize_fake(attribution, load_pipeline_config(max_iterations=1))
        assert result.total_iterations == 1
        assert result.converged is False

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

    def test_best_score_metric_is_train_simulated(self, sample_baseline):
        # fake 模式口径：模拟 train 评分（reviewer Warning：与 live 的
        # validation_pass_rate 口径不同，必须随结果携带标注）
        attribution = attribute_failures(sample_baseline.__dict__, {})
        result = run_optimize_fake(attribution, load_pipeline_config())
        assert result.best_score_metric == "train_pass_rate (simulated round)"

    def test_rounds_have_increasing_scores(self, all_fail_baseline):
        attribution = attribute_failures(all_fail_baseline.__dict__, {})
        config = load_pipeline_config(max_iterations=4)
        result = run_optimize_fake(attribution, config)
        scores = [r.score for r in result.rounds]
        # Scores should be non-decreasing (each round fixes more failures)
        for i in range(1, len(scores)):
            assert scores[i] >= scores[i - 1], f"Score decreased at round {i}"

    def test_best_so_far_is_running_max(self, all_fail_baseline):
        """best_so_far 应为历史最大分而非本轮分（reviewer Warning：旧实现
        best_so_far=score，审计字段失真）。"""
        attribution = attribute_failures(all_fail_baseline.__dict__, {})
        result = run_optimize_fake(
            attribution, load_pipeline_config(max_iterations=4))
        running = -1.0
        for r in result.rounds:
            running = max(running, r.score)
            assert r.best_so_far == running, (
                f"best_so_far 应为历史最大 {running}，得到 {r.best_so_far}")


class TestAnchorToExampleDir:
    """Tests for _anchor_to_example_dir() — live 相对路径锚定到 example 目录。

    reviewer Warning：live 模式传给 SDK 的 train/val 相对路径（默认
    `data/...`）按 CWD 解析，从仓库根等非 example 目录运行时 FileNotFoundError
    并静默降级；与 prompt_dir 一致的锚定使相对路径始终解析到 example 目录。
    """

    def _example_dir(self) -> str:
        import os
        import pipeline.optimize as opt
        # pipeline/optimize.py → pipeline → eval_optimize_loop（example 目录）
        return os.path.dirname(os.path.dirname(os.path.abspath(opt.__file__)))

    def test_relative_path_anchored(self):
        import os
        out = _anchor_to_example_dir("data/train.evalset.json")
        assert os.path.isabs(out)
        assert out == os.path.join(self._example_dir(), "data", "train.evalset.json")

    def test_absolute_path_unchanged(self):
        _abs = "/abs/data/train.evalset.json"
        assert _anchor_to_example_dir(_abs) == _abs

    def test_same_anchoring_as_prompt_dir(self):
        # 与 prompt_dir 的锚定口径一致：config.prompt_dir 相对路径锚定后
        # 与 train/val 落在同一 example 目录下（reviewer Warning 提到二者不一致）
        import os
        assert _anchor_to_example_dir("data/prompts") == os.path.join(
            self._example_dir(), "data", "prompts")
