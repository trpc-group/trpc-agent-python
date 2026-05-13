# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TDD tests for AgentEvaluator call_agent routing."""

from __future__ import annotations

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import CallbackResult
from trpc_agent_sdk.evaluation import Callbacks
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalConfig
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import Invocation
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


@pytest.mark.asyncio
async def test_evaluate_eval_set_with_call_agent_minimal():
    eval_set = EvalSet(
        eval_set_id="s1",
        eval_cases=[EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])],
    )
    eval_config = EvalConfig(criteria={"final_response_avg_score": 1.0})

    async def call_agent(query: str) -> str:
        return "world"

    failed_summary, details, result_lines, eval_results = await AgentEvaluator.evaluate_eval_set(
        eval_set,
        call_agent=call_agent,
        eval_config=eval_config,
        print_detailed_results=False,
    )

    assert failed_summary is None
    assert details == []
    assert result_lines
    assert "c1" in eval_results


@pytest.mark.asyncio
async def test_call_agent_with_agent_module_raises():
    eval_set = EvalSet(
        eval_set_id="s1",
        eval_cases=[EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])],
    )
    eval_config = EvalConfig(criteria={"final_response_avg_score": 1.0})

    async def call_agent(query: str) -> str:
        return "world"

    with pytest.raises(ValueError, match="mutually exclusive"):
        await AgentEvaluator.evaluate_eval_set(
            eval_set,
            agent_module="fake.module",
            call_agent=call_agent,
            eval_config=eval_config,
        )


@pytest.mark.asyncio
async def test_call_agent_with_runner_raises():
    eval_set = EvalSet(
        eval_set_id="s1",
        eval_cases=[EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])],
    )
    eval_config = EvalConfig(criteria={"final_response_avg_score": 1.0})

    async def call_agent(query: str) -> str:
        return "world"

    with pytest.raises(ValueError, match="mutually exclusive"):
        await AgentEvaluator.evaluate_eval_set(
            eval_set,
            runner=object(),  # type: ignore[arg-type]
            call_agent=call_agent,
            eval_config=eval_config,
        )


@pytest.mark.asyncio
async def test_call_agent_with_trace_case_raises():
    eval_set = EvalSet(
        eval_set_id="s1",
        eval_cases=[
            EvalCase(
                eval_id="trace_case",
                eval_mode="trace",
                actual_conversation=[_invocation("hello", "world")],
            )
        ],
    )
    eval_config = EvalConfig(criteria={"final_response_avg_score": 1.0})

    async def call_agent(query: str) -> str:
        return "world"

    with pytest.raises(ValueError, match="trace_case"):
        await AgentEvaluator.evaluate_eval_set(
            eval_set,
            call_agent=call_agent,
            eval_config=eval_config,
        )


@pytest.mark.asyncio
async def test_call_agent_callbacks_e2e():
    eval_set = EvalSet(
        eval_set_id="s1",
        eval_cases=[EvalCase(eval_id="c1", conversation=[_invocation("hello", "world")])],
    )
    eval_config = EvalConfig(criteria={"final_response_avg_score": 1.0})
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

    await AgentEvaluator.evaluate_eval_set(
        eval_set,
        call_agent=call_agent,
        eval_config=eval_config,
        callbacks=callbacks,
    )

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
