# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TDD tests for RemoteEvalService."""

from __future__ import annotations

import asyncio

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import CallbackResult
from trpc_agent_sdk.evaluation import Callbacks
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalMetric
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import InMemoryEvalSetsManager
from trpc_agent_sdk.evaluation import InferenceConfig
from trpc_agent_sdk.evaluation import InferenceRequest
from trpc_agent_sdk.evaluation import InferenceStatus
from trpc_agent_sdk.evaluation import Invocation
from trpc_agent_sdk.evaluation import EvaluateConfig
from trpc_agent_sdk.evaluation import EvaluateRequest
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation._remote_eval_service import RemoteEvalService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _content(text: str) -> Content:
    return Content(parts=[Part(text=text)])


def _invocation(user: str, expected: str | None = None) -> Invocation:
    return Invocation(
        invocation_id="i",
        user_content=_content(user),
        final_response=_content(expected) if expected is not None else None,
    )


def _make_manager(eval_set: EvalSet, app_name: str = "app") -> InMemoryEvalSetsManager:
    mgr = InMemoryEvalSetsManager()
    mgr.create_eval_set(app_name=app_name, eval_set_id=eval_set.eval_set_id)
    for case in eval_set.eval_cases:
        mgr.add_eval_case(app_name=app_name, eval_set_id=eval_set.eval_set_id, eval_case=case)
    return mgr


@pytest.mark.asyncio
async def test_perform_inference_async_callable_one_turn():
    case = EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])
    eval_set = EvalSet(eval_set_id="s1", eval_cases=[case])
    mgr = _make_manager(eval_set)

    async def call_agent(query: str) -> str:
        assert query == "hello"
        return "world"

    service = RemoteEvalService(call_agent=call_agent, eval_sets_manager=mgr)
    req = InferenceRequest(app_name="app", eval_set_id="s1", inference_config=InferenceConfig(parallelism=2))

    results = [r async for r in service.perform_inference(req)]

    assert len(results) == 1
    assert results[0].status == InferenceStatus.SUCCESS
    assert results[0].inferences is not None
    assert results[0].inferences[0].final_response is not None
    assert results[0].inferences[0].final_response.parts[0].text == "world"


def test_reject_sync_callable_raises_value_error():
    case = EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])
    eval_set = EvalSet(eval_set_id="s1", eval_cases=[case])
    mgr = _make_manager(eval_set)

    def call_agent(query: str) -> str:
        return query

    with pytest.raises(ValueError, match="async function"):
        RemoteEvalService(call_agent=call_agent, eval_sets_manager=mgr)


@pytest.mark.asyncio
async def test_reject_trace_cases_raises_value_error():
    trace_case = EvalCase(
        eval_id="trace_case",
        eval_mode="trace",
        actual_conversation=[_invocation("u", "a")],
    )
    eval_set = EvalSet(eval_set_id="s1", eval_cases=[trace_case])
    mgr = _make_manager(eval_set)

    async def call_agent(query: str) -> str:
        return query

    service = RemoteEvalService(call_agent=call_agent, eval_sets_manager=mgr)
    req = InferenceRequest(app_name="app", eval_set_id="s1", inference_config=InferenceConfig(parallelism=1))

    with pytest.raises(ValueError, match="trace_case"):
        _ = [r async for r in service.perform_inference(req)]


@pytest.mark.asyncio
async def test_reject_tool_trajectory_metric_raises_value_error():
    case = EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])
    eval_set = EvalSet(eval_set_id="s1", eval_cases=[case])
    mgr = _make_manager(eval_set)

    async def call_agent(query: str) -> str:
        return "world"

    service = RemoteEvalService(call_agent=call_agent, eval_sets_manager=mgr)
    req = InferenceRequest(app_name="app", eval_set_id="s1", inference_config=InferenceConfig(parallelism=1))
    inference_results = [r async for r in service.perform_inference(req)]
    evaluate_req = EvaluateRequest(
        inference_results=inference_results,
        evaluate_config=EvaluateConfig(
            eval_metrics=[EvalMetric(metric_name="tool_trajectory_avg_score", threshold=1.0)],
        ),
    )

    with pytest.raises(ValueError, match="tool_trajectory_avg_score"):
        _ = [r async for r in service.evaluate(evaluate_req)]


@pytest.mark.asyncio
async def test_case_fail_soft_when_call_agent_raises():
    case = EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])
    eval_set = EvalSet(eval_set_id="s1", eval_cases=[case])
    mgr = _make_manager(eval_set)

    async def call_agent(query: str) -> str:
        raise RuntimeError("boom")

    service = RemoteEvalService(call_agent=call_agent, eval_sets_manager=mgr)
    req = InferenceRequest(app_name="app", eval_set_id="s1", inference_config=InferenceConfig(parallelism=1))

    results = [r async for r in service.perform_inference(req)]

    assert len(results) == 1
    assert results[0].status == InferenceStatus.FAILURE
    assert results[0].error_message == "boom"


@pytest.mark.asyncio
async def test_case_parallel_turn_serial():
    case1 = EvalCase(
        eval_id="c1",
        conversation=[_invocation("q1", "a1"), _invocation("q2", "a2")],
    )
    case2 = EvalCase(
        eval_id="c2",
        conversation=[_invocation("x1", "y1"), _invocation("x2", "y2")],
    )
    eval_set = EvalSet(eval_set_id="s1", eval_cases=[case1, case2])
    mgr = _make_manager(eval_set)
    call_order: list[str] = []
    lock = asyncio.Lock()

    async def call_agent(query: str) -> str:
        async with lock:
            call_order.append(query)
        await asyncio.sleep(0.01)
        return f"resp:{query}"

    service = RemoteEvalService(call_agent=call_agent, eval_sets_manager=mgr)
    req = InferenceRequest(app_name="app", eval_set_id="s1", inference_config=InferenceConfig(parallelism=2))
    results = [r async for r in service.perform_inference(req)]

    assert len(results) == 2
    # Per-case should remain serial; globally interleaving is allowed.
    c1_order = [q for q in call_order if q in {"q1", "q2"}]
    c2_order = [q for q in call_order if q in {"x1", "x2"}]
    assert c1_order == ["q1", "q2"]
    assert c2_order == ["x1", "x2"]


@pytest.mark.asyncio
async def test_callbacks_all_nodes_called():
    case = EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])
    eval_set = EvalSet(eval_set_id="s1", eval_cases=[case])
    mgr = _make_manager(eval_set)
    points: list[str] = []

    def _cb(name: str):
        async def _fn(_ctx: dict, _args: object):
            points.append(name)
            return CallbackResult(context={"point": name})
        return _fn

    callbacks = Callbacks()
    callbacks.register_before_inference_set("t", _cb("before_inference_set"))
    callbacks.register_after_inference_set("t", _cb("after_inference_set"))
    callbacks.register_before_inference_case("t", _cb("before_inference_case"))
    callbacks.register_after_inference_case("t", _cb("after_inference_case"))
    callbacks.register_before_evaluate_set("t", _cb("before_evaluate_set"))
    callbacks.register_after_evaluate_set("t", _cb("after_evaluate_set"))
    callbacks.register_before_evaluate_case("t", _cb("before_evaluate_case"))
    callbacks.register_after_evaluate_case("t", _cb("after_evaluate_case"))

    async def call_agent(query: str) -> str:
        return "world"

    service = RemoteEvalService(call_agent=call_agent, eval_sets_manager=mgr, callbacks=callbacks)
    req = InferenceRequest(app_name="app", eval_set_id="s1", inference_config=InferenceConfig(parallelism=1))
    inference_results = [r async for r in service.perform_inference(req)]
    eval_req = EvaluateRequest(
        inference_results=inference_results,
        evaluate_config=EvaluateConfig(
            eval_metrics=[EvalMetric(metric_name="final_response_avg_score", threshold=1.0)],
        ),
    )
    eval_results = [r async for r in service.evaluate(eval_req)]

    assert len(eval_results) == 1
    assert eval_results[0].final_eval_status == EvalStatus.PASSED
    assert {
        "before_inference_set",
        "after_inference_set",
        "before_inference_case",
        "after_inference_case",
        "before_evaluate_set",
        "after_evaluate_set",
        "before_evaluate_case",
        "after_evaluate_case",
    }.issubset(set(points))
