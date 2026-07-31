"""TraceBackend / LiveBackend 满足 EvalBackend Protocol，及异常处理行为。

TraceBackend.evaluate 只应吞掉 SDK 的 _EvaluationCasesFailed（评测 case 有
失败是基线场景的预期信号），其他 AssertionError（如第三方库断言）必须向上
传播——否则半成品结果会被当成真实报告送入门控。
"""

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline._eval_backend import EvalBackend, TraceBackend, LiveBackend
from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult


class _FakeExecuter:
    """替代 AgentEvaluator.get_executer 返回值的测试替身。"""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    async def evaluate(self):
        if self._error is not None:
            raise self._error

    def get_result(self):
        return self._result


def _write_metrics(tmp_path: Path, content: str) -> str:
    p = tmp_path / "metrics.json"
    p.write_text(content)
    return str(p)


def _patch_executer(monkeypatch, executer):
    monkeypatch.setattr(
        "pipeline._eval_backend.AgentEvaluator.get_executer",
        lambda **kwargs: executer,
    )


def test_trace_backend_is_eval_backend():
    assert isinstance(TraceBackend(), EvalBackend)


def test_live_backend_is_eval_backend():
    assert isinstance(LiveBackend(), EvalBackend)


async def test_trace_backend_propagates_unrelated_assertion_error(monkeypatch, tmp_path):
    """评测过程中与 case 失败无关的 AssertionError 必须向上传播，不得吞掉。"""
    executer = _FakeExecuter(error=AssertionError("internal invariant broken"))
    _patch_executer(monkeypatch, executer)

    with pytest.raises(AssertionError):
        await TraceBackend().evaluate(
            eval_set_path=str(tmp_path / "eval.json"),
            metrics_config_path=_write_metrics(tmp_path, '{"metrics": []}'),
        )


async def test_trace_backend_swallows_evaluation_cases_failed(monkeypatch, tmp_path):
    """_EvaluationCasesFailed（case 失败信号）应被吞掉，结果仍返回给门控。"""
    result = EvaluateResult()
    executer = _FakeExecuter(result=result, error=_EvaluationCasesFailed("2 cases failed"))
    _patch_executer(monkeypatch, executer)

    raw, report = await TraceBackend().evaluate(
        eval_set_path=str(tmp_path / "eval.json"),
        metrics_config_path=_write_metrics(tmp_path, '{"metrics": []}'),
    )

    assert raw is result
    assert report is not None


async def test_trace_backend_raises_runtime_error_when_no_result(monkeypatch, tmp_path):
    """评测未产出任何结果时应报错，而不是把空结果当报告。"""
    executer = _FakeExecuter(result=None)
    _patch_executer(monkeypatch, executer)

    with pytest.raises(RuntimeError):
        await TraceBackend().evaluate(
            eval_set_path=str(tmp_path / "eval.json"),
            metrics_config_path=_write_metrics(tmp_path, '{"metrics": []}'),
        )


async def test_trace_backend_invalid_metrics_config_leaves_no_temp_file(monkeypatch, tmp_path):
    """非法 metrics 配置应让 ValidationError 向上传播，且不残留临时文件。"""
    before = set(Path(tempfile.gettempdir()).glob("tmp*.json"))

    with pytest.raises(ValidationError):
        await TraceBackend().evaluate(
            eval_set_path=str(tmp_path / "eval.json"),
            metrics_config_path=_write_metrics(tmp_path, '{"metrics": "not-a-list"}'),
        )

    after = set(Path(tempfile.gettempdir()).glob("tmp*.json"))
    assert after == before
