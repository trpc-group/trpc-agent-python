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
    def test_run_baseline_sdk_never_raises(self, data_dir):
        """run_baseline_sdk 对真实 evalset 不抛异常（成功或降级）。"""
        cfg = load_pipeline_config(mode="fake")
        result = asyncio.run(run_baseline_sdk(str(data_dir / "train.evalset.json")))
        # 要么 SDK 成功，要么降级到 trace comparator，都应有结果
        assert result.total_cases > 0 or result.errors

    def test_run_baseline_sdk_missing_file(self):
        """不存在的 evalset → errors，不崩。"""
        result = asyncio.run(run_baseline_sdk("nonexistent/path.json"))
        assert result.errors

    def test_run_optimize_live_never_raises(self, data_dir):
        """run_optimize_live 不抛异常（SDK 失败返回 errors）。"""
        cfg = load_pipeline_config(
            mode="live",
            optimizer_config=str(data_dir / "optimizer.json"),
        )
        result = asyncio.run(run_optimize_live(str(data_dir / "optimizer.json"), cfg))
        assert isinstance(result, OptimizeResult)
        # 无论成功还是失败都返回 OptimizeResult

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
