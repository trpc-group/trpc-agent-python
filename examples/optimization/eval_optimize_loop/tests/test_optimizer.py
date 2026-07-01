"""Phase 3 Optimizer ????"""

import json
import asyncio
from pathlib import Path

import pytest
from src.baseline import BaselineRunner, BaselineResult, BaselineCaseResult, BaselineSummary
from src.attribution import AttributionRunner, AttributionReport
from src.optimizer import (
    FakeOptimizer,
    OptimizationRunner,
    OptimizationResult,
    PromptCandidate,
    run_optimization,
    BASE_PROMPTS,
    CATEGORY_OPTIMIZATION_HINTS,
)


# ?? Fixtures ????????????????????????????????????????????

@pytest.fixture
def fake_attr_report():
    """? fake baseline + attribution ?????????"""
    loop = asyncio.new_event_loop()
    try:
        br = BaselineRunner(mode="fake")
        base = Path(__file__).parent.parent / "config"
        results = loop.run_until_complete(br.run(
            base / "train.evalset.json",
            base / "val.evalset.json",
        ))
        ar = AttributionRunner()
        report = ar.run(results["train"], results["val"])
        return report
    finally:
        loop.close()


@pytest.fixture
def empty_attr_report():
    """?????????"""
    return AttributionReport(total_failures=0)


@pytest.fixture
def single_cluster_report():
    """???????? ? ?????????"""
    from src.attribution import AttributionCluster
    cluster = AttributionCluster(
        category="final_answer_mismatch", priority=1,
        count=3, train_count=1, val_count=2,
        cases=["train_003", "val_002", "val_003"],
        avg_confidence=0.87, avg_score=0.35,
        dominant_condition="noise", prompt_target="system_prompt",
    )
    return AttributionReport(
        total_failures=3, train_failures=1, val_failures=2,
        attributed_count=3, unattributed_count=0,
        clusters=[cluster], optimization_priority=["final_answer_mismatch"],
    )


# ?? ?????? ????????????????????????????????????????

class TestPromptCandidate:
    def test_to_dict(self):
        c = PromptCandidate(
            candidate_id="cand_0_abc_123",
            iteration=0, target_prompt_type="system_prompt",
            prompt_before="hello", prompt_after="hello world",
            change_log=["added world"], failure_category="format_invalid",
            attribution_confidence=0.85, estimated_cost=0.0005,
        )
        d = c.to_dict()
        assert d["candidate_id"] == "cand_0_abc_123"
        assert d["iteration"] == 0
        assert d["change_log"] == ["added world"]
        assert d["prompt_after"] == "hello world"

    def test_unique_ids(self):
        """???????? ID?"""
        c1 = PromptCandidate(
            candidate_id="id1", iteration=0, target_prompt_type="system_prompt",
            prompt_before="a", prompt_after="b",
        )
        c2 = PromptCandidate(
            candidate_id="id2", iteration=1, target_prompt_type="system_prompt",
            prompt_before="a", prompt_after="b",
        )
        assert c1.candidate_id != c2.candidate_id


class TestOptimizationResult:
    def test_latest_candidate(self):
        c1 = PromptCandidate(candidate_id="c1", iteration=0, target_prompt_type="system_prompt", prompt_before="x", prompt_after="y")
        c2 = PromptCandidate(candidate_id="c2", iteration=1, target_prompt_type="system_prompt", prompt_before="y", prompt_after="z")
        result = OptimizationResult(candidates=[c1, c2], total_iterations=2)
        assert result.latest_candidate.candidate_id == "c2"
        assert result.optimized_prompt == "z"
        assert result.optimized_prompt_type == "system_prompt"

    def test_empty_no_latest(self):
        result = OptimizationResult()
        assert result.latest_candidate is None
        assert result.optimized_prompt is None

    def test_to_dict(self):
        c = PromptCandidate(candidate_id="c1", iteration=0, target_prompt_type="skill_prompt", prompt_before="x", prompt_after="y")
        result = OptimizationResult(candidates=[c], total_iterations=1)
        d = result.to_dict()
        assert d["total_iterations"] == 1
        assert len(d["candidates"]) == 1


# ?? FakeOptimizer ?? ??????????????????????????????????

class TestFakeOptimizer:
    def test_optimize_generates_candidate(self, fake_attr_report):
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        assert result.total_iterations >= 1
        assert len(result.candidates) >= 1

    def test_prompt_after_longer_than_before(self, fake_attr_report):
        """??? prompt ????????"""
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        for c in result.candidates:
            assert len(c.prompt_after) > len(c.prompt_before), (
                f"{c.target_prompt_type}: before={len(c.prompt_before)} after={len(c.prompt_after)}"
            )

    def test_change_log_not_empty(self, fake_attr_report):
        """????????????"""
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        for c in result.candidates:
            assert len(c.change_log) >= 2, f"change_log too short: {c.change_log}"

    def test_target_prompt_type_valid(self, fake_attr_report):
        """target_prompt_type ????????"""
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        for c in result.candidates:
            assert c.target_prompt_type in BASE_PROMPTS, (
                f"unknown prompt type: {c.target_prompt_type}"
            )

    def test_failure_category_mapped(self, fake_attr_report):
        """failure_category ?????????"""
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        valid = set(CATEGORY_OPTIMIZATION_HINTS.keys())
        for c in result.candidates:
            assert c.failure_category in valid, f"unknown category: {c.failure_category}"

    def test_matches_attribution_priority(self, fake_attr_report):
        """??????????????"""
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        # ??????????????
        if fake_attr_report.optimization_priority:
            top_priority = fake_attr_report.optimization_priority[0]
            assert result.candidates[0].failure_category == top_priority

    def test_max_iterations_respected(self, fake_attr_report):
        """max_iterations ????????"""
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report, max_iterations=1)
        assert len(result.candidates) <= 1

    def test_empty_attribution_no_candidates(self, empty_attr_report):
        opt = FakeOptimizer()
        result = opt.optimize(empty_attr_report)
        assert result.total_iterations == 0
        assert len(result.candidates) == 0

    def test_candidate_id_format(self, fake_attr_report):
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        for c in result.candidates:
            assert c.candidate_id.startswith("cand_"), f"bad id: {c.candidate_id}"
            assert len(c.candidate_id) > 20

    def test_attribution_summary_present(self, fake_attr_report):
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        assert "primary_failure" in result.attribution_summary
        assert "total_failures" in result.attribution_summary

    def test_strategy_label(self, fake_attr_report):
        opt = FakeOptimizer()
        result = opt.optimize(fake_attr_report)
        assert result.strategy == "failure_driven"

    def test_skill_prompt_optimization(self, single_cluster_report):
        """?????? skill_prompt???? skill_prompt?"""
        # ?? cluster ? prompt_target ? skill_prompt
        single_cluster_report.clusters[0].prompt_target = "skill_prompt"
        single_cluster_report.clusters[0].category = "knowledge_recall_insufficient"
        opt = FakeOptimizer()
        result = opt.optimize(single_cluster_report)
        assert result.candidates[0].target_prompt_type == "skill_prompt"


# ?? OptimizationRunner ?? ?????????????????????????????

class TestOptimizationRunner:
    def test_fake_mode(self, fake_attr_report):
        runner = OptimizationRunner(mode="fake")
        result = runner.run(fake_attr_report)
        assert isinstance(result, OptimizationResult)
        assert result.total_iterations >= 1

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            OptimizationRunner(mode="production")

    def test_real_mode_not_implemented(self, fake_attr_report):
        """Real ??????? NotImplementedError ? ImportError?"""
        runner = OptimizationRunner(mode="real")
        with pytest.raises((NotImplementedError, ImportError)):
            runner.run(fake_attr_report)


# ?? ?????? ????????????????????????????????????????

class TestConvenienceFunction:
    def test_run_optimization(self, fake_attr_report):
        result = run_optimization(fake_attr_report, mode="fake")
        assert isinstance(result, OptimizationResult)

    def test_run_optimization_with_config(self, fake_attr_report):
        config_path = Path(__file__).parent.parent / "config" / "optimizer.json"
        result = run_optimization(fake_attr_report, mode="fake", config_path=config_path)
        assert result.total_iterations >= 1


# ?? BASE_PROMPTS ??? ?????????????????????????????????

class TestBasePrompts:
    def test_all_prompt_types_have_content(self):
        for ptype, text in BASE_PROMPTS.items():
            assert len(text) > 50, f"{ptype} prompt too short"

    def test_system_prompt_has_key_sections(self):
        sp = BASE_PROMPTS["system_prompt"]
        assert "????" in sp
        assert "????" in sp
        assert "???" in sp

    def test_skill_prompt_has_key_sections(self):
        sp = BASE_PROMPTS["skill_prompt"]
        assert "???" in sp
        assert "??" in sp
        assert "??" in sp
        assert "???" in sp


# ?? ??????? ??????????????????????????????????????

class TestPipelineIntegration:
    """baseline ? attribution ? optimizer ??????"""

    @pytest.mark.asyncio
    async def test_full_fake_pipeline(self):
        """?? fake pipeline ????"""
        base = Path(__file__).parent.parent / "config"

        # Phase 1: baseline
        br = BaselineRunner(mode="fake")
        results = await br.run(
            base / "train.evalset.json",
            base / "val.evalset.json",
        )
        assert results["train"].summary.total == 3
        assert results["val"].summary.total == 3

        # Phase 2: attribution
        ar = AttributionRunner()
        attr_report = ar.run(results["train"], results["val"])
        assert attr_report.total_failures >= 1
        assert attr_report.unattributed_count == 0

        # Phase 3: optimizer
        opt = FakeOptimizer()
        opt_result = opt.optimize(attr_report)
        assert opt_result.total_iterations >= 1
        assert opt_result.latest_candidate is not None

        # ???????
        pipeline_output = {
            "baseline": {
                "train": results["train"].to_dict(),
                "val": results["val"].to_dict(),
            },
            "attribution": attr_report.to_dict(),
            "optimization": opt_result.to_dict(),
        }
        json_str = json.dumps(pipeline_output, ensure_ascii=False, indent=2)
        assert len(json_str) > 1000
        parsed = json.loads(json_str)
        assert "optimization" in parsed
