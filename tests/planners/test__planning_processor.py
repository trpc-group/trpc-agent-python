# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for PlanningProcessor."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from trpc_agent_sdk.abc import PlannerABC as BasePlanner
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.planners._built_in_planner import BuiltInPlanner
from trpc_agent_sdk.planners._plan_re_act_planner import PlanReActPlanner
from trpc_agent_sdk.planners._planning_processor import (
    PlanningProcessor,
    default_planning_processor,
)
from trpc_agent_sdk.types import Content, GenerateContentConfig, Part, ThinkingConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(name="test_agent", planner=None):
    """Create a mock agent with .name and .planner attributes."""
    agent = Mock()
    agent.name = name
    agent.planner = planner
    return agent


def _make_agent_no_planner_attr(name="test_agent"):
    """Create a mock agent without the 'planner' attribute at all."""
    agent = Mock(spec=[])
    agent.name = name
    return agent


def _make_ctx(agent=None):
    """Create a mock InvocationContext."""
    ctx = Mock()
    ctx.invocation_id = "inv-1"
    if agent is None:
        agent = _make_agent()
    ctx.agent = agent
    return ctx


@pytest.fixture
def processor():
    return PlanningProcessor()


# ---------------------------------------------------------------------------
# default_planning_processor module-level instance
# ---------------------------------------------------------------------------


class TestDefaultInstance:
    def test_is_planning_processor(self):
        assert isinstance(default_planning_processor, PlanningProcessor)


# ---------------------------------------------------------------------------
# _get_planner
# ---------------------------------------------------------------------------


class TestGetPlanner:
    def test_returns_none_when_agent_has_no_planner_attr(self, processor):
        agent = _make_agent_no_planner_attr()
        assert processor._get_planner(agent) is None

    def test_returns_none_when_planner_is_none(self, processor):
        agent = _make_agent(planner=None)
        assert processor._get_planner(agent) is None

    def test_returns_none_when_planner_is_falsy(self, processor):
        agent = _make_agent(planner=0)
        assert processor._get_planner(agent) is None

    def test_returns_planner_when_plan_react(self, processor):
        planner = PlanReActPlanner()
        agent = _make_agent(planner=planner)
        assert processor._get_planner(agent) is planner

    def test_returns_planner_when_builtin(self, processor):
        planner = BuiltInPlanner(thinking_config=ThinkingConfig(include_thoughts=True))
        agent = _make_agent(planner=planner)
        assert processor._get_planner(agent) is planner

    def test_fallback_to_plan_react_when_not_base_planner(self, processor):
        agent = _make_agent(planner="not_a_real_planner")
        result = processor._get_planner(agent)
        assert isinstance(result, PlanReActPlanner)


# ---------------------------------------------------------------------------
# _remove_thought_from_request
# ---------------------------------------------------------------------------


class TestRemoveThoughtFromRequest:
    def test_removes_thought_from_parts(self, processor):
        part = Part(text="thinking", thought=True)
        request = LlmRequest(contents=[Content(parts=[part])])

        processor._remove_thought_from_request(request)

        assert part.thought is None

    def test_handles_empty_contents(self, processor):
        request = LlmRequest(contents=[])
        processor._remove_thought_from_request(request)

    def test_handles_none_contents(self, processor):
        request = LlmRequest()
        request.contents = None
        processor._remove_thought_from_request(request)

    def test_handles_content_without_parts(self, processor):
        request = LlmRequest(contents=[Content(parts=None)])
        processor._remove_thought_from_request(request)

    def test_removes_thought_from_multiple_parts(self, processor):
        parts = [
            Part(text="t1", thought=True),
            Part(text="t2", thought=True),
            Part(text="t3"),
        ]
        request = LlmRequest(contents=[Content(parts=parts)])

        processor._remove_thought_from_request(request)

        for part in parts:
            assert part.thought is None

    def test_removes_thought_from_multiple_contents(self, processor):
        p1 = Part(text="a", thought=True)
        p2 = Part(text="b", thought=True)
        request = LlmRequest(contents=[
            Content(parts=[p1]),
            Content(parts=[p2]),
        ])

        processor._remove_thought_from_request(request)

        assert p1.thought is None
        assert p2.thought is None


# ---------------------------------------------------------------------------
# _create_error_event
# ---------------------------------------------------------------------------


class TestCreateErrorEvent:
    def test_creates_event_with_correct_fields(self, processor):
        agent = _make_agent(name="my_agent")
        ctx = _make_ctx(agent)

        event = processor._create_error_event(ctx, "err_code", "err_msg")

        assert event.error_code == "err_code"
        assert event.error_message == "err_msg"
        assert event.author == "my_agent"
        assert event.invocation_id == "inv-1"

    def test_creates_event_instance(self, processor):
        ctx = _make_ctx()
        event = processor._create_error_event(ctx, "c", "m")
        assert isinstance(event, Event)


# ---------------------------------------------------------------------------
# process_request
# ---------------------------------------------------------------------------


class TestProcessRequest:
    def test_returns_none_when_no_planner(self, processor):
        agent = _make_agent(planner=None)
        ctx = _make_ctx(agent)
        request = LlmRequest()

        result = processor.process_request(request, agent, ctx)

        assert result is None

    def test_returns_none_when_no_planner_attr(self, processor):
        agent = _make_agent_no_planner_attr()
        ctx = _make_ctx(agent)
        request = LlmRequest()

        result = processor.process_request(request, agent, ctx)

        assert result is None

    def test_applies_thinking_config_for_builtin_planner(self, processor):
        tc = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc)
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        request = LlmRequest()

        result = processor.process_request(request, agent, ctx)

        assert result is None
        assert request.config is not None
        assert request.config.thinking_config is tc

    def test_appends_instruction_for_plan_react_planner(self, processor):
        planner = PlanReActPlanner()
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        request = LlmRequest()

        result = processor.process_request(request, agent, ctx)

        assert result is None
        assert request.config is not None
        assert request.config.system_instruction is not None
        assert len(request.config.system_instruction) > 0

    def test_removes_thought_from_request(self, processor):
        planner = PlanReActPlanner()
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        part = Part(text="old thought", thought=True)
        request = LlmRequest(contents=[Content(parts=[part])])

        processor.process_request(request, agent, ctx)

        assert part.thought is None

    def test_returns_error_event_on_exception(self, processor):
        planner = Mock(spec=PlanReActPlanner)
        planner.build_planning_instruction.side_effect = RuntimeError("boom")
        agent = _make_agent(name="err_agent", planner=planner)
        ctx = _make_ctx(agent)
        request = LlmRequest()

        result = processor.process_request(request, agent, ctx)

        assert result is not None
        assert isinstance(result, Event)
        assert result.error_code == "planning_request_error"
        assert "boom" in result.error_message

    def test_builtin_planner_returns_no_instruction(self, processor):
        tc = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc)
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        request = LlmRequest()

        processor.process_request(request, agent, ctx)

        has_sys_instr = (
            request.config
            and request.config.system_instruction
            and len(str(request.config.system_instruction)) > 0
        )
        assert not has_sys_instr


# ---------------------------------------------------------------------------
# process_response
# ---------------------------------------------------------------------------


class TestProcessResponse:
    def test_returns_none_when_no_parts(self, processor):
        agent = _make_agent()
        ctx = _make_ctx(agent)

        result = processor.process_response([], agent, ctx)

        assert result is None

    def test_returns_none_when_none_parts(self, processor):
        agent = _make_agent()
        ctx = _make_ctx(agent)

        result = processor.process_response(None, agent, ctx)

        assert result is None

    def test_returns_none_when_no_planner(self, processor):
        agent = _make_agent(planner=None)
        ctx = _make_ctx(agent)
        parts = [Part(text="hello")]

        result = processor.process_response(parts, agent, ctx)

        assert result is None

    def test_processes_parts_with_plan_react_planner(self, processor):
        planner = PlanReActPlanner()
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        parts = [Part(text="/*PLANNING*/my plan/*FINAL_ANSWER*/the answer")]

        result = processor.process_response(parts, agent, ctx)

        assert result is not None
        assert len(result) >= 1

    def test_passes_partial_flag_from_event(self, processor):
        planner = Mock(spec=PlanReActPlanner)
        planner.process_planning_response.return_value = [Part(text="processed")]
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        event = Event(invocation_id="inv-1", author="test", partial=True)
        parts = [Part(text="streaming")]

        processor.process_response(parts, agent, ctx, event=event)

        planner.process_planning_response.assert_called_once_with(ctx, parts, True)

    def test_partial_false_when_no_event(self, processor):
        planner = Mock(spec=PlanReActPlanner)
        planner.process_planning_response.return_value = [Part(text="processed")]
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        parts = [Part(text="data")]

        processor.process_response(parts, agent, ctx)

        planner.process_planning_response.assert_called_once_with(ctx, parts, False)

    def test_returns_none_when_planner_returns_none(self, processor):
        planner = Mock(spec=PlanReActPlanner)
        planner.process_planning_response.return_value = None
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        parts = [Part(text="hello")]

        result = processor.process_response(parts, agent, ctx)

        assert result is None

    def test_returns_original_parts_on_exception(self, processor):
        planner = Mock(spec=PlanReActPlanner)
        planner.process_planning_response.side_effect = RuntimeError("fail")
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        parts = [Part(text="data")]

        result = processor.process_response(parts, agent, ctx)

        assert result is parts

    def test_builtin_planner_returns_none(self, processor):
        planner = BuiltInPlanner(thinking_config=ThinkingConfig(include_thoughts=True))
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        parts = [Part(text="response")]

        result = processor.process_response(parts, agent, ctx)

        assert result is None

    def test_returns_none_for_empty_parts_list(self, processor):
        planner = PlanReActPlanner()
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)

        result = processor.process_response([], agent, ctx)

        assert result is None

    def test_event_without_partial_defaults_none(self, processor):
        planner = Mock(spec=PlanReActPlanner)
        planner.process_planning_response.return_value = [Part(text="ok")]
        agent = _make_agent(planner=planner)
        ctx = _make_ctx(agent)
        event = Event(invocation_id="inv-1", author="test")
        parts = [Part(text="data")]

        processor.process_response(parts, agent, ctx, event=event)

        planner.process_planning_response.assert_called_once_with(ctx, parts, None)
