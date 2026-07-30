#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""验证 _run_evaluator 不吞非 case-failure 断言，以及复现命令的 shell 引用。"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

import pytest

import pipeline
import runner
from trpc_agent_sdk.evaluation import EvaluateResult


class _StubExecuter:
    """evaluate() 抛 AssertionError；get_result() 返回预置结果。"""

    def __init__(self, result):
        self._result = result

    async def evaluate(self):
        raise AssertionError("boom")

    def get_result(self):
        return self._result


@pytest.mark.asyncio
async def test_assertion_without_result_is_not_swallowed(tmp_path: Path, monkeypatch) -> None:
    """配置/契约类断言（结果尚未生成）必须重新抛出，不能伪装成空结果。"""

    stub = _StubExecuter(result=None)
    monkeypatch.setattr(
        runner.AgentEvaluator, "get_executer", staticmethod(lambda *a, **k: stub)
    )
    evalset = tmp_path / "set.evalset.json"
    evalset.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="aborted before producing results"):
        await runner._run_evaluator(evalset)


@pytest.mark.asyncio
async def test_case_failure_assertion_keeps_structured_result(tmp_path: Path, monkeypatch) -> None:
    """case 失败断言仍返回 executer 里的结构化结果。"""

    expected = EvaluateResult(results_by_eval_set_id={})
    stub = _StubExecuter(result=expected)
    monkeypatch.setattr(
        runner.AgentEvaluator, "get_executer", staticmethod(lambda *a, **k: stub)
    )
    evalset = tmp_path / "set.evalset.json"
    evalset.write_text("{}", encoding="utf-8")

    result = await runner._run_evaluator(evalset)
    assert result is expected


def test_repro_cmd_quotes_paths_with_spaces() -> None:
    """含空格的路径必须被引用，复现命令可安全粘贴执行。"""

    args = argparse.Namespace(
        mode="fake",
        scenario="success",
        candidate_file="C:/tmp/my candidate.md",
        apply=True,
        optimizer_config=None,
    )
    cmd = pipeline._build_repro_cmd(args)
    parsed = shlex.split(cmd)
    assert "--candidate-file=C:/tmp/my candidate.md" in parsed
    assert "--apply" in parsed
    assert not any(part == "candidate.md" for part in parsed)
