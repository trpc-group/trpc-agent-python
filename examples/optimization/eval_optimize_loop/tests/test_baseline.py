"""Tests for baseline evaluation module."""

import asyncio
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
        # legacy 无 actual_conversation 的 case 按通过处理但不计入 pass_rate
        # （unreviewed=True，与 SDK 路径 NOT_EVALUATED 一致），避免未评测样本
        # 虚高通过率、污染 gate 的 improvement 判定。
        path = temp_json_file({
            "eval_set_id": "test",
            "eval_cases": [
                {"eval_id": "c1", "conversation": [{"text": "hello"}]},
                {"eval_id": "c2", "conversation": [{"text": "world"}]},
            ],
        })
        try:
            result = run_baseline_fake(path, pipeline_config)
            assert result.total_cases == 0
            assert result.passed_cases == 0
            assert result.pass_rate == 0.0
            # 未评测 case 仍保留在 per_case 供审计，标记 unreviewed=True
            by_id = {r["eval_id"]: r for r in result.per_case_results}
            assert len(by_id) == 2
            assert by_id["c1"]["pass"] is True
            assert by_id["c1"]["unreviewed"] is True
            assert by_id["c2"]["pass"] is True
            assert by_id["c2"]["unreviewed"] is True
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
            # 补强：fail_case 缺 conversation → 应判 MISSING_EXPECTED_OUTPUT 失败
            by_id = {r["eval_id"]: r for r in result.per_case_results}
            assert by_id["fail_case"]["pass"] is False
            assert by_id["fail_case"]["category"] == "missing_expected_output"
            assert by_id["pass_case"]["pass"] is True
        finally:
            os.unlink(path)


class TestRunBaselineSdk:
    """Tests for SDK baseline path."""

    def test_sdk_stub_returns_result(self):
        # run_baseline_sdk 是 async；不存在的文件路径应返回 errors（不崩）
        result = asyncio.run(run_baseline_sdk("some/path.json"))
        assert isinstance(result, BaselineResult)
        # 文件不存在 → error recorded
        assert len(result.errors) > 0

    def test_sdk_metric_breakdown_uses_approximated_field(self, monkeypatch, tmp_path):
        """SDK 路径无 per-case score：final_response_avg_score 用 pass_rate 兜底时
        必须以 *_approximated 后缀与 fake 路径同名字段隔离（reviewer Warning：
        下游按同名字段取值会把 SDK 兜底值误当作 per-case 均值）。"""
        import sys
        import json

        class _CR:
            def __init__(self, status, err=""):
                self.final_eval_status = status
                self.error_message = err

        class _FakeEvalStatus:
            PASSED = "passed"
            NOT_EVALUATED = "not_evaluated"

        class _FakeAgentEvaluator:
            @classmethod
            async def evaluate_eval_set(cls, eval_set, call_agent=None,
                                        eval_config=None,
                                        print_detailed_results=False):
                return None, None, None, {
                    "c1": [_CR(_FakeEvalStatus.PASSED)],
                    "c2": [_CR(_FakeEvalStatus.NOT_EVALUATED)],
                }

        class _FakeEvalSet:
            @classmethod
            def model_validate_json(cls, s):
                return object()

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", type(
            "M", (), {
                "AgentEvaluator": _FakeAgentEvaluator,
                "EvalSet": _FakeEvalSet,
                "EvalStatus": _FakeEvalStatus,
            }))
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics",
            type("ME", (), {"EvalStatus": _FakeEvalStatus}))

        path = tmp_path / "evalset.json"
        path.write_text(json.dumps({"eval_set_id": "t", "eval_cases": []}),
                        encoding="utf-8")
        result = asyncio.run(run_baseline_sdk(str(path), eval_config=object()))
        assert result.total_cases == 1  # NOT_EVALUATED 不计入
        assert result.pass_rate == 1.0
        # 兜底字段必须改名隔离；fake 路径的 final_response_avg_score 语义不同
        assert "final_response_avg_score" not in result.metric_breakdown
        assert result.metric_breakdown["final_response_avg_score_approximated"] == 1.0

    def test_sdk_falls_back_to_trace_comparator(self, monkeypatch):
        # SDK 不可用（强制 ImportError）→ 确定性降级到 trace comparator，产生有意义结果。
        # 仓库内有 trpc_agent_sdk 源码包，若不 monkeypatch 会走真实 SDK 路径而非降级。
        import sys
        import json
        import tempfile
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", None)
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation._eval_metrics", None)
        cases = [{
            "eval_id": "c1",
            "eval_mode": "trace",
            "conversation": [{
                "user_content": {"parts": [{"text": "25 + 17"}], "role": "user"},
                "final_response": {"parts": [{"text": "42"}], "role": "model"},
            }],
            "actual_conversation": [{
                "user_content": {"parts": [{"text": "25 + 17"}], "role": "user"},
                "final_response": {"parts": [{"text": "42"}], "role": "model"},
            }],
        }]
        data = {"eval_set_id": "test", "eval_cases": cases}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            path = f.name
        try:
            result = asyncio.run(run_baseline_sdk(path))
            assert isinstance(result, BaselineResult)
            assert result.total_cases == 1
            # 降级路径：errors 说明 SDK 不可用且回退到 trace comparator
            assert any("SDK AgentEvaluator not available" in e for e in result.errors)
        finally:
            os.unlink(path)


class TestFakeAgentDivByZero:
    """fake agent 除零不产生非有限浮点（inf 无法 JSON 序列化、comparator 不可预期）。"""

    def test_div_by_zero_returns_finite(self):
        import json
        from agent.agent import run_agent
        result = run_agent("5 / 0")
        resp = str(result.get("final_response", ""))
        assert "inf" not in resp
        # 可被标准 json.dumps 序列化（不含 Infinity/NaN）
        json.dumps(result)
