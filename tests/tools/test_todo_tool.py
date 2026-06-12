# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for :mod:`trpc_agent_sdk.tools._todo_tool`."""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.tools._todo_tool import (
    DEFAULT_STATE_KEY_PREFIX,
    TodoItem,
    TodoStatus,
    TodoWriteTool,
    get_todos,
    render_todos,
    state_key,
    validate_todos,
)
from trpc_agent_sdk.types import Content, EventActions, Part, State


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield


def _sample_todos(**statuses: str) -> list[dict]:
    defaults = {
        "step1": ("Run step 1", "Running step 1", TodoStatus.IN_PROGRESS),
        "step2": ("Run step 2", "Running step 2", TodoStatus.PENDING),
    }
    return [{
        "content": defaults[name][0],
        "activeForm": defaults[name][1],
        "status": statuses.get(name, defaults[name][2].value),
    } for name in ("step1", "step2")]


@pytest.fixture
def session_bundle():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = _StubAgent(name="todo_planner")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="",
    )
    return service, session, agent, ctx


class TestValidateTodos:
    def test_accepts_valid_list(self):
        items = [
            TodoItem(content="A", activeForm="Doing A", status=TodoStatus.IN_PROGRESS),
            TodoItem(content="B", activeForm="Doing B", status=TodoStatus.PENDING),
        ]
        assert validate_todos(items) is None

    def test_rejects_multiple_in_progress(self):
        items = [
            TodoItem(content="A", activeForm="Doing A", status=TodoStatus.IN_PROGRESS),
            TodoItem(content="B", activeForm="Doing B", status=TodoStatus.IN_PROGRESS),
        ]
        assert "in_progress" in (validate_todos(items) or "")

    def test_rejects_duplicate_content(self):
        items = [
            TodoItem(content="Same", activeForm="Doing same", status=TodoStatus.IN_PROGRESS),
            TodoItem(content="Same", activeForm="Still same", status=TodoStatus.PENDING),
        ]
        assert "duplicates" in (validate_todos(items) or "")


class TestStateKey:
    def test_default_prefix_without_branch(self):
        assert state_key(DEFAULT_STATE_KEY_PREFIX, "") == "todos"

    def test_appends_branch(self):
        assert state_key(DEFAULT_STATE_KEY_PREFIX, "todo_planner") == "todos:todo_planner"


class TestProcessRequest:
    @pytest.mark.asyncio
    async def test_process_request_adds_instructions(self, session_bundle):
        _, _, _, ctx = session_bundle
        tool = TodoWriteTool()
        llm_request = LlmRequest()

        await tool.process_request(tool_context=ctx, llm_request=llm_request)

        assert tool.name in llm_request.tools_dict
        assert llm_request.config is not None
        assert llm_request.config.system_instruction is not None
        assert "todo_write" in str(llm_request.config.system_instruction).lower()


class TestTodoWriteTool:
    @pytest.mark.asyncio
    async def test_writes_and_returns_echo(self, session_bundle):
        _, _, agent, ctx = session_bundle
        tool = TodoWriteTool(clear_on_all_done=False)
        payload = _sample_todos()

        result = await tool._run_async_impl(tool_context=ctx, args={"todos": payload})

        assert "error" not in result
        assert len(result["todos"]) == 2
        assert result["oldTodos"] is None
        key = state_key(DEFAULT_STATE_KEY_PREFIX, agent.name)
        assert key in ctx.state._delta

    @pytest.mark.asyncio
    async def test_reads_previous_list_on_second_call(self, session_bundle):
        _, _, agent, ctx = session_bundle
        tool = TodoWriteTool(clear_on_all_done=False)
        first = await tool._run_async_impl(tool_context=ctx, args={"todos": _sample_todos()})
        assert first["oldTodos"] is None

        second = await tool._run_async_impl(
            tool_context=ctx,
            args={"todos": _sample_todos(step1="completed", step2="in_progress")},
        )
        assert len(second["oldTodos"]) == 2
        assert second["todos"][0]["status"] == "completed"
        assert second["todos"][1]["status"] == "in_progress"
        assert state_key(DEFAULT_STATE_KEY_PREFIX, agent.name) in ctx.session.state

    @pytest.mark.asyncio
    async def test_rejects_missing_todos_field(self, session_bundle):
        _, _, _, ctx = session_bundle
        result = await TodoWriteTool()._run_async_impl(tool_context=ctx, args={})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_clear_on_all_done(self, session_bundle):
        _, _, _, ctx = session_bundle
        tool = TodoWriteTool(clear_on_all_done=True)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"todos": _sample_todos(step1="completed", step2="completed")},
        )
        assert result["todos"] == []


class TestPersistence:
    @pytest.mark.asyncio
    async def test_todos_prefix_survives_append_event_and_get_session(self, session_bundle):
        service, session, agent, ctx = session_bundle
        tool = TodoWriteTool(clear_on_all_done=False)
        await tool._run_async_impl(tool_context=ctx, args={"todos": _sample_todos()})

        event = Event(
            invocation_id="inv-1",
            author=agent.name,
            content=Content(parts=[Part.from_text(text="tool result")]),
            actions=EventActions(state_delta=dict(ctx.event_actions.state_delta)),
        )
        await service.append_event(session, event)

        stored = await service.get_session(app_name="test", user_id="u1", session_id="s1")
        todos = get_todos(stored, branch=agent.name)
        assert len(todos) == 2
        assert todos[0].status == TodoStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_temp_prefix_is_not_persisted(self, session_bundle):
        service, session, agent, ctx = session_bundle
        tool = TodoWriteTool(state_key_prefix="temp:todos", clear_on_all_done=False)
        await tool._run_async_impl(tool_context=ctx, args={"todos": _sample_todos()})

        event = Event(
            invocation_id="inv-1",
            author=agent.name,
            content=Content(parts=[Part.from_text(text="tool result")]),
            actions=EventActions(state_delta=dict(ctx.event_actions.state_delta)),
        )
        await service.append_event(session, event)

        stored = await service.get_session(app_name="test", user_id="u1", session_id="s1")
        key = state_key("temp:todos", agent.name)
        assert key not in (stored.state or {})
        assert get_todos(stored, branch=agent.name, prefix="temp:todos") == []


class TestRenderTodos:
    def test_renders_checklist(self):
        items = [
            TodoItem(content="Done task", activeForm="Doing done", status=TodoStatus.COMPLETED),
            TodoItem(content="Active task", activeForm="Doing active", status=TodoStatus.IN_PROGRESS),
            TodoItem(content="Pending task", activeForm="Doing pending", status=TodoStatus.PENDING),
        ]
        rendered = render_todos(items)
        assert "[x] Done task" in rendered
        assert "[>] Doing active" in rendered
        assert "[ ] Pending task" in rendered
