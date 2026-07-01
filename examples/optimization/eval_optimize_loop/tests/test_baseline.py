"""Phase 1 Baseline 单元测试"""

import asyncio
import json
from pathlib import Path

import pytest
from src.baseline import (
    BaselineRunner,
    BaselineResult,
    BaselineCaseResult,
    BaselineSummary,
    run_baseline,
)


class TestBaselineDataStructures:
    """数据结构测试"""

    def test_case_result_to_dict(self):
        r = BaselineCaseResult(
            case_id="test_001",
            image="plate_001.jpg",
            ground_truth="京A12345",
            predicted="京A12345",
            score=1.0,
            passed=True,
            correct=True,
            char_correct=7,
            char_total=7,
        )
        d = r.to_dict()
        assert d["case_id"] == "test_001"
        assert d["score"] == 1.0
        assert d["passed"] is True

    def test_summary_to_dict(self):
        s = BaselineSummary(total=3, passed=2, failed=1, avg_score=0.75, pass_rate=0.667)
        d = s.to_dict()
        assert d["total"] == 3
        assert d["passed"] == 2

    def test_result_score_map(self):
        result = BaselineResult(
            dataset_name="test",
            cases=[
                BaselineCaseResult(case_id="a", image="", ground_truth="", predicted="", score=0.9, passed=True, correct=True),
                BaselineCaseResult(case_id="b", image="", ground_truth="", predicted="", score=0.4, passed=False, correct=False),
            ],
        )
        sm = result.score_map
        assert sm == {"a": 0.9, "b": 0.4}

    def test_result_failed_cases(self):
        result = BaselineResult(
            dataset_name="test",
            cases=[
                BaselineCaseResult(case_id="a", image="", ground_truth="", predicted="", score=0.9, passed=True, correct=True),
                BaselineCaseResult(case_id="b", image="", ground_truth="", predicted="", score=0.4, passed=False, correct=False, failure_reason="mismatch"),
            ],
        )
        assert len(result.failed_cases) == 1
        assert result.failed_cases[0].case_id == "b"


class TestBaselineRunnerFakeMode:
    """Fake 模式集成测试"""

    @pytest.mark.asyncio
    async def test_run_train_split(self, train_evalset_path):
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(train_evalset_path, "train")
        assert isinstance(result, BaselineResult)
        assert result.dataset_name == "train"
        assert len(result.cases) == 3

    @pytest.mark.asyncio
    async def test_run_val_split(self, val_evalset_path):
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(val_evalset_path, "val")
        assert len(result.cases) == 3
        assert result.dataset_name == "val"

    @pytest.mark.asyncio
    async def test_run_both_splits(self, train_evalset_path, val_evalset_path):
        runner = BaselineRunner(mode="fake")
        results = await runner.run(train_evalset_path, val_evalset_path)
        assert "train" in results
        assert "val" in results
        assert len(results["train"].cases) == 3
        assert len(results["val"].cases) == 3

    @pytest.mark.asyncio
    async def test_train_001_should_pass(self, train_evalset_path):
        """train_001 是清晰车牌 → 基线应通过"""
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(train_evalset_path, "train")
        case = next(c for c in result.cases if c.case_id == "train_001")
        assert case.passed, f"train_001 should pass, got: {case.failure_reason}"
        assert case.correct
        assert case.score >= 0.9

    @pytest.mark.asyncio
    async def test_train_002_may_fail(self, train_evalset_path):
        """train_002 是噪声图片 → 可能失败"""
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(train_evalset_path, "train")
        case = next(c for c in result.cases if c.case_id == "train_002")
        # 噪声导致 1 字符错误，应归因
        assert not case.correct
        assert case.char_correct < case.char_total  # may_fail: ???????????

    @pytest.mark.asyncio
    async def test_val_001_critical_should_pass(self, val_evalset_path):
        """val_001 是关键 case → 基线应通过（清晰图片）"""
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(val_evalset_path, "val")
        case = next(c for c in result.cases if c.case_id == "val_001")
        assert case.passed
        assert case.correct

    @pytest.mark.asyncio
    async def test_val_003_should_fail_baseline(self, val_evalset_path):
        """val_003 是严重模糊 → 基线应失败"""
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(val_evalset_path, "val")
        case = next(c for c in result.cases if c.case_id == "val_003")
        assert not case.passed, "严重模糊基线应失败"
        assert not case.correct

    @pytest.mark.asyncio
    async def test_summary_statistics(self, val_evalset_path):
        """验证汇总统计计算正确"""
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(val_evalset_path, "val")
        s = result.summary
        assert s.total == 3
        assert s.passed + s.failed == s.total
        assert 0.0 <= s.avg_score <= 1.0
        assert 0.0 <= s.pass_rate <= 1.0
        assert s.avg_latency_ms > 0
        assert s.avg_cost > 0

    @pytest.mark.asyncio
    async def test_trajectory_present(self, train_evalset_path):
        """验证轨迹信息被正确记录"""
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(train_evalset_path, "train")
        for case in result.cases:
            assert case.trajectory, f"{case.case_id} 缺少轨迹信息"
            assert "nodes" in case.trajectory
            assert len(case.trajectory["nodes"]) > 1

    @pytest.mark.asyncio
    async def test_judge_scores_present(self, train_evalset_path):
        """验证 Judge 三维评分被正确填充"""
        runner = BaselineRunner(mode="fake")
        result = await runner.run_split(train_evalset_path, "train")
        for case in result.cases:
            assert case.judge_recognition >= 0, f"{case.case_id}: judge_recognition 未填充"
            assert case.judge_blacklist >= 0
            assert case.judge_response >= 0

    @pytest.mark.asyncio
    async def test_serializable_to_json(self, train_evalset_path, val_evalset_path):
        """验证结果可序列化为 JSON"""
        runner = BaselineRunner(mode="fake")
        results = await runner.run(train_evalset_path, val_evalset_path)
        for name in ("train", "val"):
            d = results[name].to_dict()
            json_str = json.dumps(d, ensure_ascii=False)
            parsed = json.loads(json_str)
            assert parsed["dataset_name"] == name
            assert len(parsed["cases"]) == 3

    @pytest.mark.asyncio
    async def test_convenience_function(self):
        """测试便捷函数 run_baseline()"""
        results = await run_baseline(mode="fake")
        assert "train" in results
        assert "val" in results
        assert results["train"].summary.total == 3


class TestBaselineRunnerRealMode:
    """Real 模式测试"""

    @pytest.mark.asyncio
    async def test_real_mode_requires_plate_agent_root(self, train_evalset_path):
        """没有 plate_agent_root 应抛出 ValueError"""
        runner = BaselineRunner(mode="real")
        with pytest.raises(ValueError, match="plate_agent_root"):
            await runner.run_split(train_evalset_path, "train")

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            BaselineRunner(mode="production")


class TestBaselineEdgeCases:
    """边界场景"""

    @pytest.mark.asyncio
    async def test_empty_evalset_raises(self, tmp_path):
        """空数据集应抛出异常"""
        empty_path = tmp_path / "empty.json"
        empty_path.write_text('{"cases": []}', encoding="utf-8")
        runner = BaselineRunner(mode="fake")
        with pytest.raises(ValueError, match="No cases"):
            await runner.run_split(empty_path, "test")

    def test_build_summary_empty(self):
        """空列表汇总"""
        s = BaselineRunner._build_summary([])
        assert s.total == 0
        assert s.pass_rate == 0.0

    @staticmethod
    def test_parse_trajectory_basic():
        result = BaselineRunner._parse_trajectory(
            "preprocess→locate→segment→recognize(conf=0.92)→format_output"
        )
        assert result["nodes"] == ["preprocess", "locate", "segment", "recognize", "format_output"]
        assert result["confidence"] == 0.92
        assert result["human_review_triggered"] is False

    @staticmethod
    def test_parse_trajectory_with_human_review():
        result = BaselineRunner._parse_trajectory(
            "preprocess→locate→segment→recognize(conf=0.38)→human_review→format_output"
        )
        assert result["human_review_triggered"] is True
        assert "human_review" in result["nodes"]

    @staticmethod
    def test_parse_trajectory_empty():
        assert BaselineRunner._parse_trajectory("") == {}
