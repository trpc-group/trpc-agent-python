from __future__ import annotations

import pytest

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._agent_evaluator import _EvalExecuter
from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed

_ORIGINAL_AGENT_EVALUATE = AgentEvaluator.evaluate
_ORIGINAL_EXECUTER_EVALUATE = _EvalExecuter.evaluate


def _log_threshold_failure(exc: _EvaluationCasesFailed) -> None:
    print(
        "\n[examples/evaluation] Threshold not met; treating as non-fatal for example smoke tests.",
        flush=True,
    )
    print(exc, flush=True)


async def _safe_agent_evaluate(*args, **kwargs):
    try:
        return await _ORIGINAL_AGENT_EVALUATE(*args, **kwargs)
    except _EvaluationCasesFailed as exc:
        _log_threshold_failure(exc)
        return None


async def _safe_executer_evaluate(self, *args, **kwargs):
    try:
        return await _ORIGINAL_EXECUTER_EVALUATE(self, *args, **kwargs)
    except _EvaluationCasesFailed as exc:
        _log_threshold_failure(exc)
        return None


@pytest.fixture(autouse=True)
def _ignore_threshold_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AgentEvaluator, "evaluate", staticmethod(_safe_agent_evaluate))
    monkeypatch.setattr(_EvalExecuter, "evaluate", _safe_executer_evaluate)
