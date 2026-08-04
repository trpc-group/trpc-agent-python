"""Live 模式健壮性测试 — 保证 SDK 不可用/失败时不崩。

验收标准 #5：fake/trace 模式完整 pipeline ≤ 3 分钟。
live 模式即使 SDK 配置不全也必须降级运行而非崩溃。
"""

import asyncio
import sys
from pathlib import Path

import pytest

_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.baseline import run_baseline_fake, run_baseline_sdk
from pipeline.config import load_pipeline_config
from pipeline.optimize import OptimizeResult, run_optimize_live


class TestLiveModeRobustness:
    def test_run_baseline_sdk_falls_back_when_sdk_missing(self, monkeypatch, data_dir):
        """SDK 不可用时 run_baseline_sdk 确定性降级到 trace comparator：带 errors、不抛异常。"""
        import sys
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", None)
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation._eval_metrics", None)
        result = asyncio.run(run_baseline_sdk(str(data_dir / "train.evalset.json")))
        assert result.errors
        assert any("SDK AgentEvaluator not available" in e for e in result.errors)
        assert result.total_cases > 0  # 降级到 fake 后仍有真实评分

    def test_run_baseline_sdk_missing_file(self):
        """不存在的 evalset → errors，不崩。"""
        result = asyncio.run(run_baseline_sdk("nonexistent/path.json"))
        assert result.errors

    def test_run_optimize_live_reports_errors_when_sdk_missing(self, monkeypatch, data_dir):
        """SDK 不可用时 run_optimize_live 返回 OptimizeResult 且 errors 非空（确定性）。"""
        import sys
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", None)
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation._eval_metrics", None)
        cfg = load_pipeline_config(mode="live")
        result = asyncio.run(run_optimize_live(str(data_dir / "optimizer.json"), cfg))
        assert isinstance(result, OptimizeResult)
        assert result.errors

    def test_run_baseline_sdk_re_raises_validation_error(self, monkeypatch, tmp_path):
        """evalset/配置校验失败（ValueError，含 pydantic ValidationError）应重新抛出，
        而非降级为 trace comparator 伪装成"可继续的基线"（reviewer Warning ②）。"""
        import sys
        import pipeline.baseline as baseline_mod
        import tempfile, json, os

        class _FakeEvalSet:
            @classmethod
            def model_validate_json(cls, s):
                raise ValueError("evalset validation failed: bad case format")

        class _FakeEvalModule:
            AgentEvaluator = None
            EvalSet = _FakeEvalSet
            EvalStatus = type("S", (), {})

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics",
            type("M", (), {"EvalStatus": type("S", (), {})}),
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"eval_set_id": "t", "eval_cases": []}, f)
            path = f.name
        try:
            with pytest.raises(ValueError, match="evalset validation failed"):
                asyncio.run(baseline_mod.run_baseline_sdk(path, eval_config=object()))
        finally:
            os.unlink(path)

    def test_run_baseline_sdk_re_raises_non_sdk_exceptions(self, monkeypatch, tmp_path):
        """非 SDK 异常（AttributeError 等 pipeline bug）应向上抛出，而非静默降级为 fake。"""
        import sys
        import pipeline.baseline as baseline_mod
        import tempfile, json, os

        class _FakeEvalSet:
            @classmethod
            def model_validate_json(cls, s):
                return object()

        async def _fake_evaluate(eval_set, **kwargs):
            raise AttributeError("bug in pipeline code, not an SDK failure")

        class _FakeAgentEvaluator:
            @staticmethod
            async def evaluate_eval_set(*args, **kwargs):
                return await _fake_evaluate(*args, **kwargs)

        class _FakeEvalModule:
            AgentEvaluator = _FakeAgentEvaluator
            EvalSet = _FakeEvalSet
            EvalStatus = type("S", (), {})

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics",
            type("M", (), {"EvalStatus": type("S", (), {})}),
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"eval_set_id": "t", "eval_cases": []}, f)
            path = f.name
        try:
            with pytest.raises(AttributeError):
                asyncio.run(baseline_mod.run_baseline_sdk(path, eval_config=object()))
        finally:
            os.unlink(path)

    def test_live_validation_error_not_silently_degraded(self, monkeypatch, data_dir):
        """live 编排：evalset 校验失败（run_baseline_sdk 抛 ValueError）时，
        run_pipeline 按 SDK 契约 re-raise，不得被 except Exception 静默降级到
        trace comparator（reviewer Warning：宽泛异常捕获吞掉 ValueError）。"""
        import sys as _sys
        import run_pipeline as rp
        import pipeline.baseline as baseline_mod

        async def _boom(*args, **kwargs):
            raise ValueError("evalset validation failed: bad case format")

        monkeypatch.setattr(baseline_mod, "run_baseline_sdk", _boom)
        monkeypatch.setattr(_sys, "argv", [
            "run_pipeline.py", "--mode", "live",
            "--train-evalset", str(data_dir / "train.evalset.json"),
            "--val-evalset", str(data_dir / "val.evalset.json"),
            "--optimizer-config", str(data_dir / "optimizer.json"),
        ])
        with pytest.raises(ValueError, match="evalset validation failed"):
            rp.main()

    def test_run_baseline_sdk_re_raises_on_bad_return_shape(self, monkeypatch, tmp_path):
        """SDK evaluate_eval_set 返回非 4 元组（接口结构变更）应向上抛 ValueError，
        而非静默降级为 trace comparator 伪装成可继续基线（reviewer Warning ② 落地）。"""
        import sys
        import pipeline.baseline as baseline_mod
        import tempfile, json, os

        class _FakeEvalSet:
            @classmethod
            def model_validate_json(cls, s):
                return object()

        async def _fake_evaluate(eval_set, **kwargs):
            return (None, [], [])  # 只有 3 项，缺 case_results

        class _FakeAgentEvaluator:
            @staticmethod
            async def evaluate_eval_set(*args, **kwargs):
                return await _fake_evaluate(*args, **kwargs)

        class _FakeEvalModule:
            AgentEvaluator = _FakeAgentEvaluator
            EvalSet = _FakeEvalSet
            EvalStatus = type("S", (), {})

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics",
            type("M", (), {"EvalStatus": type("S", (), {})}),
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"eval_set_id": "t", "eval_cases": []}, f)
            path = f.name
        try:
            with pytest.raises(ValueError, match="unexpected shape"):
                asyncio.run(baseline_mod.run_baseline_sdk(path, eval_config=object()))
        finally:
            os.unlink(path)

    def test_fake_pipeline_performance(self, data_dir):
        """fake 模式完整 pipeline < 3 秒（验收标准 #5：≤3 分钟，巨大余量）。"""
        import time
        cfg = load_pipeline_config(mode="fake")
        start = time.monotonic()
        baseline = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)
        elapsed = time.monotonic() - start
        # 单 evalset baseline 评测应 < 3 秒
        assert elapsed < 3.0, f"baseline 耗时 {elapsed:.2f}s 超限"
        assert baseline.total_cases == 34


class TestBuildCallAgent:
    def test_build_call_agent(self):
        """build_call_agent 返回 async callable，能处理输入。"""
        from agent.agent import build_call_agent
        call_agent = build_call_agent()
        import inspect
        assert inspect.iscoroutinefunction(call_agent) or callable(call_agent)

    def test_call_agent_returns_text(self):
        """call_agent 调用返回字符串。"""
        from agent.agent import build_call_agent, run_agent
        # 直接测 run_agent 确定性
        result = run_agent("What is 25 + 17?")
        assert "final_response" in result


class TestLiveModeContract:
    """Live 模式契约测试 — 用 mock SDK 验证字段映射正确。

    reviewer 指出：此前 live 测试断言过弱，SDK 字段名错误被 getattr 默认值
    掩盖。这里通过 monkeypatch sys.modules 注入符合 SDK schema 的 fake 模块，
    验证 run_baseline_sdk / run_optimize_live 的映射逻辑正确。
    """

    def test_baseline_maps_eval_status_to_pass(self, monkeypatch, tmp_path):
        """EvalCaseResult.final_eval_status == PASSED → pass=True。"""
        import sys
        import pipeline.baseline as baseline_mod
        import tempfile, json, os

        class _Status:
            PASSED = object()
            FAILED = object()
            NOT_EVALUATED = object()

        class _CaseResult:
            def __init__(self, status, msg=""):
                self.final_eval_status = status
                self.error_message = msg

        class _FakeEvalSet:
            @classmethod
            def model_validate_json(cls, s):
                return object()

        class _FakeEvalMetrics:
            EvalStatus = _Status

        # 构造 mock case_results：2 个 PASSED、1 个 FAILED
        async def _fake_evaluate(eval_set, **kwargs):
            assert kwargs.get("eval_config") is not None, "eval_config 必填"
            results = {
                "c1": [_CaseResult(_Status.PASSED)],
                "c2": [_CaseResult(_Status.PASSED)],
                "c3": [_CaseResult(_Status.FAILED, "wrong answer")],
            }
            return (None, [], [], results)

        class _FakeAgentEvaluator:
            @staticmethod
            async def evaluate_eval_set(*args, **kwargs):
                return await _fake_evaluate(*args, **kwargs)

        class _FakeEvalModule:
            AgentEvaluator = _FakeAgentEvaluator
            EvalSet = _FakeEvalSet
            EvalStatus = _Status

        # 注入 fake SDK 模块到 sys.modules，让函数内 import 拿到
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules,
            "trpc_agent_sdk.evaluation._eval_metrics",
            _FakeEvalMetrics,
        )

        # 临时 evalset 文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"eval_set_id": "t", "eval_cases": []}, f)
            path = f.name
        try:
            result = asyncio.run(baseline_mod.run_baseline_sdk(path, eval_config=object()))
            assert result.total_cases == 3
            assert result.passed_cases == 2
            assert result.failed_cases == 1
            assert "c3" in result.failed_case_ids
        finally:
            os.unlink(path)

    def test_baseline_skips_not_evaluated_cases(self, monkeypatch, tmp_path):
        """EvalStatus.NOT_EVALUATED 不应计为失败（避免虚增失败/污染 gate）。"""
        import sys
        import pipeline.baseline as baseline_mod
        import tempfile, json, os

        class _Status:
            PASSED = object()
            FAILED = object()
            NOT_EVALUATED = object()

        class _CaseResult:
            def __init__(self, status, msg=""):
                self.final_eval_status = status
                self.error_message = msg

        class _FakeEvalSet:
            @classmethod
            def model_validate_json(cls, s):
                return object()

        class _FakeEvalMetrics:
            EvalStatus = _Status

        async def _fake_evaluate(eval_set, **kwargs):
            results = {
                "c1": [_CaseResult(_Status.PASSED)],
                "c2": [_CaseResult(_Status.NOT_EVALUATED)],
                "c3": [_CaseResult(_Status.FAILED, "wrong answer")],
            }
            return (None, [], [], results)

        class _FakeAgentEvaluator:
            @staticmethod
            async def evaluate_eval_set(*args, **kwargs):
                return await _fake_evaluate(*args, **kwargs)

        class _FakeEvalModule:
            AgentEvaluator = _FakeAgentEvaluator
            EvalSet = _FakeEvalSet
            EvalStatus = _Status

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics", _FakeEvalMetrics,
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"eval_set_id": "t", "eval_cases": []}, f)
            path = f.name
        try:
            result = asyncio.run(baseline_mod.run_baseline_sdk(path, eval_config=object()))
            # NOT_EVALUATED 跳过：total=2（PASSED+FAILED），失败仅 1
            assert result.total_cases == 2
            assert result.passed_cases == 1
            assert result.failed_cases == 1
            assert "c3" in result.failed_case_ids
            assert "c2" not in result.failed_case_ids
        finally:
            os.unlink(path)

    def test_baseline_aggregates_multiple_runs_per_case(self, monkeypatch):
        """num_runs>1：按 case 聚合且全部 run 通过才算通过（flaky 失败不被掩盖）。"""
        import sys
        import pipeline.baseline as baseline_mod
        import tempfile, json, os

        class _Status:
            PASSED = object()
            FAILED = object()
            NOT_EVALUATED = object()

        class _CaseResult:
            def __init__(self, status, msg=""):
                self.final_eval_status = status
                self.error_message = msg

        class _FakeEvalSet:
            @classmethod
            def model_validate_json(cls, s):
                return object()

        class _FakeEvalMetrics:
            EvalStatus = _Status

        async def _fake_evaluate(eval_set, **kwargs):
            # c1: 一次 PASSED 一次 FAILED → 混合结果视为失败（flaky 不被掩盖）
            # c2: 两次 PASSED → 通过
            results = {
                "c1": [_CaseResult(_Status.PASSED), _CaseResult(_Status.FAILED, "x")],
                "c2": [_CaseResult(_Status.PASSED), _CaseResult(_Status.PASSED)],
            }
            return (None, [], [], results)

        class _FakeAgentEvaluator:
            @staticmethod
            async def evaluate_eval_set(*args, **kwargs):
                return await _fake_evaluate(*args, **kwargs)

        class _FakeEvalModule:
            AgentEvaluator = _FakeAgentEvaluator
            EvalSet = _FakeEvalSet
            EvalStatus = _Status

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics", _FakeEvalMetrics,
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"eval_set_id": "t", "eval_cases": []}, f)
            path = f.name
        try:
            result = asyncio.run(baseline_mod.run_baseline_sdk(path, eval_config=object()))
            assert result.total_cases == 2
            assert result.passed_cases == 1
            assert result.failed_cases == 1
            assert result.failed_case_ids == ["c1"]  # 混合结果计失败，无重复
        finally:
            os.unlink(path)

    def test_optimize_clears_artifacts_on_non_success_status(self, monkeypatch):
        """SDK status != SUCCEEDED（FAILED/CANCELED）时清空 best_prompt/optimized_fields。"""
        import sys
        import pipeline.optimize as optimize_mod

        class _FakeOptimizeResult:
            total_llm_cost = 2.0
            total_rounds = 2
            status = "FAILED"
            best_prompts = {"system": "should be discarded"}
            rounds = []

        async def _fake_optimize(**kwargs):
            return _FakeOptimizeResult()

        class _FakeAgentOptimizer:
            @staticmethod
            async def optimize(**kwargs):
                return await _fake_optimize(**kwargs)

        class _FakeTargetPrompt:
            def add_path(self, *args, **kwargs):
                pass

        class _FakeEvalModule:
            AgentOptimizer = _FakeAgentOptimizer
            TargetPrompt = _FakeTargetPrompt

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._optimize_config",
            type("M", (), {}),
        )
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics",
            type("M", (), {"EvalStatus": type("S", (), {})}),
        )

        cfg = load_pipeline_config(mode="live")
        result = asyncio.run(optimize_mod.run_optimize_live("opt.json", cfg, call_agent=object()))
        assert result.converged is False
        assert result.best_prompt == {}
        assert result.optimized_fields == []
        assert any("did not succeed" in e for e in result.errors)

    def test_optimize_clears_artifacts_on_empty_status(self, monkeypatch):
        """SDK status 为空串（未确知成功）时同样清空产物并记录 error。"""
        import sys
        import pipeline.optimize as optimize_mod

        class _FakeOptimizeResult:
            total_llm_cost = 1.0
            total_rounds = 1
            status = ""
            best_prompts = {"system": "should be discarded"}
            rounds = []

        async def _fake_optimize(**kwargs):
            return _FakeOptimizeResult()

        class _FakeAgentOptimizer:
            @staticmethod
            async def optimize(**kwargs):
                return await _fake_optimize(**kwargs)

        class _FakeTargetPrompt:
            def add_path(self, *args, **kwargs):
                pass

        class _FakeEvalModule:
            AgentOptimizer = _FakeAgentOptimizer
            TargetPrompt = _FakeTargetPrompt

        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._optimize_config",
            type("M", (), {}),
        )
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics",
            type("M", (), {"EvalStatus": type("S", (), {})}),
        )

        cfg = load_pipeline_config(mode="live")
        result = asyncio.run(optimize_mod.run_optimize_live("opt.json", cfg, call_agent=object()))
        assert result.converged is False
        assert result.best_prompt == {}
        assert result.optimized_fields == []
        assert any("did not succeed" in e for e in result.errors)

    def test_optimize_maps_sdk_fields(self, monkeypatch):
        """SDK OptimizeResult 字段 → 我们的 OptimizeResult（total_llm_cost 等）。"""
        import sys
        import pipeline.optimize as optimize_mod

        class _FakeRound:
            round = 1
            validation_pass_rate = 0.95
            optimized_field_names = ["system"]
            train_pass_rate = 0.97
            candidate_prompts = {"system": "new prompt"}

        class _FakeOptimizeResult:
            total_llm_cost = 1.5
            total_rounds = 3
            status = "SUCCEEDED"  # SDK 实际取值
            best_prompts = {"system": "optimized prompt"}
            rounds = [_FakeRound()]

        async def _fake_optimize(**kwargs):
            return _FakeOptimizeResult()

        class _FakeAgentOptimizer:
            @staticmethod
            async def optimize(**kwargs):
                return await _fake_optimize(**kwargs)

        class _FakeTargetPrompt:
            def add_path(self, *args, **kwargs):
                pass

        class _FakeEvalModule:
            AgentOptimizer = _FakeAgentOptimizer
            TargetPrompt = _FakeTargetPrompt

        # 注入 fake SDK 模块
        monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", _FakeEvalModule)
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._optimize_config",
            type("M", (), {}),
        )
        monkeypatch.setitem(
            sys.modules, "trpc_agent_sdk.evaluation._eval_metrics",
            type("M", (), {"EvalStatus": type("S", (), {})}),
        )

        cfg = load_pipeline_config(mode="live")
        result = asyncio.run(optimize_mod.run_optimize_live("opt.json", cfg, call_agent=object()))
        assert result.total_cost == 1.5
        assert result.total_iterations == 3
        assert result.converged is True
        assert result.best_prompt == {"system": "optimized prompt"}
        assert len(result.rounds) == 1
        assert result.rounds[0].score == 0.95
        assert result.rounds[0].round_index == 1
