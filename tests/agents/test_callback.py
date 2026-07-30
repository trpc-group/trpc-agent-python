# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for callback filters."""

from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.agents._callback import (
    AgentCallbackFilter,
    CallbackFilter,
    ModelCallbackFilter,
    ToolCallbackFilter,
)
from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.filter import FilterResult, FilterType
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield


def _make_ctx():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = _StubAgent(name="test_agent")
    return InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )


# ---------------------------------------------------------------------------
# CallbackFilter.canonical_callbacks
# ---------------------------------------------------------------------------


class TestCanonicalCallbacks:
    def test_none_returns_empty(self):
        assert CallbackFilter.canonical_callbacks(None) == []

    def test_empty_list_returns_empty(self):
        assert CallbackFilter.canonical_callbacks([]) == []

    def test_single_callback_wrapped_in_list(self):
        cb = lambda ctx: None
        result = CallbackFilter.canonical_callbacks(cb)
        assert result == [cb]

    def test_list_returned_as_is(self):
        cb1 = lambda ctx: None
        cb2 = lambda ctx: None
        result = CallbackFilter.canonical_callbacks([cb1, cb2])
        assert result == [cb1, cb2]


# ---------------------------------------------------------------------------
# AgentCallbackFilter
# ---------------------------------------------------------------------------


class TestAgentCallbackFilter:
    def test_init_sets_type_and_name(self):
        f = AgentCallbackFilter(None, None)
        assert f._type == FilterType.AGENT
        assert f._name == "agent_callback"

    def test_init_with_single_callbacks(self):
        before_cb = lambda ctx: None
        after_cb = lambda ctx: None
        f = AgentCallbackFilter(before_cb, after_cb)
        assert len(f._before_callback) == 1
        assert len(f._after_callback) == 1

    def test_init_with_list_callbacks(self):
        before_cbs = [lambda ctx: None, lambda ctx: None]
        after_cbs = [lambda ctx: None]
        f = AgentCallbackFilter(before_cbs, after_cbs)
        assert len(f._before_callback) == 2
        assert len(f._after_callback) == 1


class TestAgentCallbackFilterBefore:
    @pytest.fixture
    def setup_ctx(self):
        return _make_ctx()

    def test_before_no_callbacks_returns_none(self, setup_ctx):
        f = AgentCallbackFilter(None, None)
        rsp = FilterResult()

        async def run():
            await f._before(setup_ctx.agent_context, None, rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())
        assert rsp.rsp is None

    def test_before_sync_callback_returns_content(self, setup_ctx):
        content = Content(parts=[Part(text="intercepted")])
        before_cb = lambda ctx: content
        f = AgentCallbackFilter(before_cb, None)
        rsp = FilterResult()

        async def run():
            await f._before(setup_ctx.agent_context, None, rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())

        assert rsp.rsp is not None
        assert isinstance(rsp.rsp, Event)
        assert rsp.is_continue is False

    def test_before_async_callback(self, setup_ctx):
        content = Content(parts=[Part(text="async result")])

        async def async_cb(ctx):
            return content

        f = AgentCallbackFilter(async_cb, None)
        rsp = FilterResult()

        async def run():
            await f._before(setup_ctx.agent_context, None, rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())

        assert rsp.rsp is not None
        assert rsp.is_continue is False

    def test_before_callback_returning_none_continues(self, setup_ctx):
        before_cb = lambda ctx: None
        f = AgentCallbackFilter(before_cb, None)
        rsp = FilterResult()

        async def run():
            await f._before(setup_ctx.agent_context, None, rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())

        assert rsp.rsp is None


class TestAgentCallbackFilterAfter:
    @pytest.fixture
    def setup_ctx(self):
        return _make_ctx()

    def test_after_no_callbacks_returns_none(self, setup_ctx):
        f = AgentCallbackFilter(None, None)
        rsp = FilterResult()

        async def run():
            await f._after(setup_ctx.agent_context, None, rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())
        assert rsp.rsp is None

    def test_after_callback_returns_event(self, setup_ctx):
        content = Content(parts=[Part(text="after result")])
        after_cb = lambda ctx: content
        f = AgentCallbackFilter(None, after_cb)
        rsp = FilterResult()

        async def run():
            await f._after(setup_ctx.agent_context, None, rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())
        assert rsp.rsp is not None


# ---------------------------------------------------------------------------
# ModelCallbackFilter
# ---------------------------------------------------------------------------


class TestModelCallbackFilter:
    def test_init_sets_model_type(self):
        f = ModelCallbackFilter(None, None)
        assert f._type == FilterType.MODEL
        assert f._name == "model_callback"


class TestModelCallbackFilterBefore:
    @pytest.fixture
    def setup_ctx(self):
        return _make_ctx()

    def test_before_no_callbacks(self, setup_ctx):
        f = ModelCallbackFilter(None, None)
        rsp = FilterResult()

        async def run():
            await f._before(setup_ctx.agent_context, LlmRequest(), rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())
        assert rsp.rsp is None

    def test_before_callback_returns_response(self, setup_ctx):
        mock_response = LlmResponse(content=Content(parts=[Part(text="intercepted")]))

        def before_cb(ctx, req):
            return mock_response

        f = ModelCallbackFilter(before_cb, None)
        rsp = FilterResult()

        async def run():
            await f._before(setup_ctx.agent_context, LlmRequest(), rsp)

        with patch("trpc_agent_sdk.agents._callback.get_invocation_ctx", return_value=setup_ctx):
            asyncio.run(run())
        assert rsp.rsp is mock_response
        assert rsp.is_continue is False


# ---------------------------------------------------------------------------
# ToolCallbackFilter
# ---------------------------------------------------------------------------


class TestToolCallbackFilter:
    def test_init_sets_tool_type(self):
        f = ToolCallbackFilter(None, None)
        assert f._type == FilterType.TOOL
        assert f._name == "tool_callback"
