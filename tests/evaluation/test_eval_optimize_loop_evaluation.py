"""Tests for the evaluation adapter and leakage checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation._eval_case import Invocation
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult
from trpc_agent_sdk.evaluation._eval_result import EvalMetricResult
from trpc_agent_sdk.evaluation._eval_result import EvalMetricResultPerInvocation
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from examples.optimization.eval_optimize_loop.loop.evaluation import EvaluationRequest
from examples.optimization.eval_optimize_loop.loop.evaluation import evaluate_split
from examples.optimization.eval_optimize_loop.loop.evaluation import load_eval_set
from examples.optimization.eval_optimize_loop.loop.evaluation import _snapshot_case
from examples.optimization.eval_optimize_loop.loop.evaluation import validate_inputs
from examples.optimization.eval_optimize_loop.loop.models import InputPaths
from examples.optimization.eval_optimize_loop.loop.models import SplitName

ROOT = Path("examples/optimization/eval_optimize_loop")
PROMPT = ROOT / "agent" / "prompts" / "system.md"
TRAIN = ROOT / "data" / "train.evalset.json"
VALIDATION = ROOT / "data" / "val.evalset.json"
OPTIMIZER = ROOT / "optimizer.json"
GATE = ROOT / "gate.json"


def _paths() -> InputPaths:
    return InputPaths(
        prompt_path=PROMPT,
        train_path=TRAIN,
        validation_path=VALIDATION,
        optimizer_path=OPTIMIZER,
        gate_path=GATE,
    )


def _expected_response(query: str) -> str:
    if "invoice" in query or "payment" in query:
        return '{"queue":"billing"}'
    if "password" in query:
        return '{"queue":"account"}'
    return '{"queue":"technical"}'


async def _passing_agent(query: str) -> str:
    return _expected_response(query)


async def _failing_agent(query: str) -> str:
    del query
    return '{"queue":"unknown"}'


def test_validate_inputs_rejects_split_content_leakage(tmp_path):
    train = load_eval_set(TRAIN)
    validation = load_eval_set(VALIDATION)
    duplicate = train.eval_cases[0].model_copy(update={"eval_id": "duplicate"})
    duplicate_path = tmp_path / "duplicate.evalset.json"
    duplicate_path.write_text(
        validation.model_copy(update={
            "eval_cases": [duplicate]
        }).model_dump_json(),
        encoding="utf-8",
    )
    paths = _paths().model_copy(update={"validation_path": duplicate_path})

    with pytest.raises(ValueError, match="overlap"):
        validate_inputs(paths)


def test_validate_inputs_returns_hashes_and_gate():
    bundle, optimizer, gate = validate_inputs(_paths())

    assert bundle.prompt_path == PROMPT.resolve()
    assert set(bundle.hashes) == {
        PROMPT.name,
        TRAIN.name,
        VALIDATION.name,
        OPTIMIZER.name,
        GATE.name,
    }
    assert optimizer.evaluate.get_eval_metrics()[0].metric_name == gate.primary_metric


@pytest.mark.asyncio
async def test_evaluate_split_keeps_passing_result():
    snapshot = await evaluate_split(EvaluationRequest(TRAIN, OPTIMIZER, SplitName.TRAIN, _passing_agent))

    assert snapshot.primary_score == pytest.approx(1.0)
    assert snapshot.pass_rate == pytest.approx(1.0)
    assert all(case.passed for case in snapshot.cases)


@pytest.mark.asyncio
async def test_evaluate_split_keeps_failed_case_result():
    snapshot = await evaluate_split(EvaluationRequest(VALIDATION, OPTIMIZER, SplitName.VALIDATION, _failing_agent))

    assert snapshot.primary_score == pytest.approx(0.0)
    assert snapshot.pass_rate == pytest.approx(0.0)
    assert all(not case.passed for case in snapshot.cases)
    assert all(not case.hard_failure for case in snapshot.cases)


def test_validate_inputs_rejects_unknown_critical_case(tmp_path):
    gate = tmp_path / "gate.json"
    gate.write_text(
        '{"primary_metric":"final_response_avg_score","critical_case_ids":["missing"]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="belong to validation"):
        validate_inputs(_paths().model_copy(update={"gate_path": gate}))


def test_metric_not_evaluated_is_a_hard_failure():
    invocation = Invocation(
        user_content=Content(role="user", parts=[Part.from_text(text="query")]),
        final_response=Content(role="model", parts=[Part.from_text(text="answer")]),
    )
    metric = EvalMetricResult(
        metric_name="final_response_avg_score",
        threshold=1.0,
        score=None,
        eval_status=EvalStatus.NOT_EVALUATED,
    )
    case = EvalCaseResult(
        eval_set_id="set",
        eval_id="case",
        final_eval_status=EvalStatus.PASSED,
        overall_eval_metric_results=[metric],
        eval_metric_result_per_invocation=[EvalMetricResultPerInvocation(actual_invocation=invocation)],
        session_id="session",
    )

    snapshot = _snapshot_case("case", SplitName.VALIDATION, [case], metric.metric_name)

    assert snapshot.hard_failure is True
    assert snapshot.passed is False
