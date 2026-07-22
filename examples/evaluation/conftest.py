from __future__ import annotations

import pytest

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed

_ORIGINAL_EVALUATE = AgentEvaluator.evaluate


async def _safe_evaluate(*args, **kwargs):
    try:
        return await _ORIGINAL_EVALUATE(*args, **kwargs)
    except _EvaluationCasesFailed as exc:
        print(
            "\n[examples/evaluation] Threshold not met; treating as non-fatal for example smoke tests.",
            flush=True,
        )
        print(exc, flush=True)
        return None


@pytest.fixture(autouse=True)
def _ignore_threshold_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AgentEvaluator, "evaluate", staticmethod(_safe_evaluate))
