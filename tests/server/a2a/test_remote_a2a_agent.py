# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a._remote_a2a_agent."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    Message,
    Part as A2APart,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.server.a2a._remote_a2a_agent import TrpcRemoteA2aAgent
from trpc_agent_sdk.types import Content, Part


def _make_agent_card():
    return AgentCard(
        name="remote",
        description="A remote agent",
        url="http://remote:8080",
        version="1.0",
        capabilities=AgentCapabilities(streaming=True),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        skills=[],
    )


def _make_invocation_context(**overrides):
    ctx = MagicMock(spec=InvocationContext)
    ctx.invocation_id = overrides.get("invocation_id", "inv-1")
    ctx.user_id = overrides.get("user_id", "user-1")
    ctx.branch = overrides.get("branch", None)
    ctx.run_config = overrides.get("run_config", None)
    ctx.override_messages = overrides.get("override_messages", None)
    session = MagicMock()
    session.id = "session-1"
    session.events = overrides.get("events", [])
    ctx.session = session
    ctx.raise_if_cancelled = AsyncMock()
    ctx.get_cancel_event = AsyncMock(return_value=asyncio.Event())
    return ctx


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
class TestTrpcRemoteA2aAgentInit:
    def test_with_base_url(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://remote:8080")
        assert agent.agent_base_url == "http://remote:8080"
        assert agent._initialized is False

    def test_with_agent_card(self):
        card = _make_agent_card()
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=card)
        assert agent._agent_card is card

    def test_with_a2a_client(self):
        client = MagicMock()
        agent = TrpcRemoteA2aAgent(name="remote", a2a_client=client)
        assert agent._a2a_client is client

    def test_raises_on_empty_name(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            TrpcRemoteA2aAgent(name="", agent_base_url="http://remote:8080")

    def test_raises_on_whitespace_name(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            TrpcRemoteA2aAgent(name="  ", agent_base_url="http://remote:8080")

    def test_raises_without_any_connection_info(self):
        with pytest.raises(ValueError, match="Either agent_card, a2a_client, or agent_base_url"):
            TrpcRemoteA2aAgent(name="remote")

    def test_raises_with_empty_base_url(self):
        with pytest.raises(ValueError, match="Either agent_card, a2a_client, or agent_base_url"):
            TrpcRemoteA2aAgent(name="remote", agent_base_url="  ")


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------
class TestInitialize:
    async def test_already_initialized(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._initialized = True
        result = await agent.initialize()
        assert result is True

    async def test_with_agent_card_creates_client(self):
        card = _make_agent_card()
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=card, agent_base_url="http://x")
        result = await agent.initialize()
        assert result is True
        assert agent._initialized is True
        assert agent._a2a_client is not None
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_without_card_resolves(self):
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=mock_card)
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://remote:8080")
            result = await agent.initialize()
            assert result is True
            assert agent._agent_card is mock_card
            if agent._httpx_client:
                await agent._httpx_client.aclose()

    async def test_failure_returns_false(self):
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(side_effect=Exception("connection failed"))
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://bad:1234")
            result = await agent.initialize()
            assert result is False
            assert agent._initialized is False
            if agent._httpx_client:
                await agent._httpx_client.aclose()

    async def test_populates_description_from_card(self):
        card = _make_agent_card()
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=card, agent_base_url="http://x")
        await agent.initialize()
        assert agent.description == "A remote agent"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_without_base_url_and_card_raises(self):
        card = _make_agent_card()
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=card)
        agent._agent_card = None
        agent.agent_base_url = None
        result = await agent.initialize()
        assert result is False


# ---------------------------------------------------------------------------
# _build_outgoing_message
# ---------------------------------------------------------------------------
class TestBuildOutgoingMessage:
    def test_with_override_messages(self):
        from google.genai import types as genai_types
        ctx = _make_invocation_context(
            override_messages=[genai_types.Content(role="user", parts=[genai_types.Part(text="override")])]
        )
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        msg = agent._build_outgoing_message(ctx)
        assert msg is not None

    def test_from_session_events(self):
        user_event = Event(invocation_id="inv-1", author="user",
                           content=Content(role="user", parts=[Part(text="hello")]))
        ctx = _make_invocation_context(override_messages=None, events=[user_event])
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        msg = agent._build_outgoing_message(ctx)
        assert msg is not None

    def test_no_content_returns_none(self):
        ctx = _make_invocation_context(override_messages=None, events=[])
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        msg = agent._build_outgoing_message(ctx)
        assert msg is None

    def test_no_user_event_returns_none(self):
        non_user = MagicMock()
        non_user.author = "agent"
        non_user.content = Content(role="model", parts=[Part(text="response")])
        ctx = _make_invocation_context(override_messages=None, events=[non_user])
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        msg = agent._build_outgoing_message(ctx)
        assert msg is None


# ---------------------------------------------------------------------------
# _build_message_from_artifact_event
# ---------------------------------------------------------------------------
class TestBuildMessageFromArtifactEvent:
    def test_with_artifact(self):
        event = TaskArtifactUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            artifact=Artifact(
                artifact_id="a1",
                parts=[A2APart(root=TextPart(text="result"))],
            ),
            last_chunk=False,
        )
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        msg = agent._build_message_from_artifact_event(event)
        assert msg.role == Role.agent
        assert len(msg.parts) == 1

    def test_without_artifact(self):
        from pydantic import ValidationError

        event = MagicMock()
        event.artifact = None
        delattr(event, "artifact")
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        with pytest.raises(ValidationError):
            agent._build_message_from_artifact_event(event)


# ---------------------------------------------------------------------------
# _ensure_non_streaming_for_discrete_events
# ---------------------------------------------------------------------------
class TestEnsureNonStreamingForDiscreteEvents:
    def test_function_call_sets_partial_false(self):
        from google.genai.types import FunctionCall as GenaiFunctionCall
        event = Event(invocation_id="inv-1", author="a", partial=True,
                      content=Content(role="model", parts=[Part(function_call=GenaiFunctionCall(name="fn", args={}))]))
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._ensure_non_streaming_for_discrete_events(event)
        assert event.partial is False

    def test_tool_response_object(self):
        event = Event(invocation_id="inv-1", author="a", partial=True, object="tool.response",
                      content=Content(role="model", parts=[Part(text="resp")]))
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._ensure_non_streaming_for_discrete_events(event)
        assert event.partial is False

    def test_code_execution_object(self):
        event = Event(invocation_id="inv-1", author="a", partial=True, object="postprocessing.code_execution",
                      content=Content(role="model", parts=[Part(text="code")]))
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._ensure_non_streaming_for_discrete_events(event)
        assert event.partial is False

    def test_text_event_not_changed(self):
        event = Event(invocation_id="inv-1", author="a", partial=True,
                      content=Content(role="model", parts=[Part(text="hi")]))
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._ensure_non_streaming_for_discrete_events(event)
        assert event.partial is True


# ---------------------------------------------------------------------------
# _resolve_partial
# ---------------------------------------------------------------------------
class TestResolvePartial:
    def test_none_metadata(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._resolve_partial(None) is True

    def test_no_partial_key(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._resolve_partial({"other": "val"}) is True

    def test_bool_true(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._resolve_partial({"partial": True}) is True

    def test_bool_false(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._resolve_partial({"partial": False}) is False

    def test_string_true(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._resolve_partial({"partial": "true"}) is True

    def test_string_false(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._resolve_partial({"partial": "false"}) is False

    def test_unknown_value(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._resolve_partial({"partial": 42}) is True


# ---------------------------------------------------------------------------
# _events_from_response
# ---------------------------------------------------------------------------
class TestEventsFromResponse:
    def _make_agent(self):
        return TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())

    def test_artifact_event_with_parts(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        artifact_event = TaskArtifactUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            artifact=Artifact(
                artifact_id="a1",
                parts=[A2APart(root=TextPart(text="result"))],
            ),
            last_chunk=False,
        )
        events = agent._events_from_response(artifact_event, 1, ctx)
        assert len(events) == 1

    def test_artifact_event_empty_last_chunk_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        artifact_event = TaskArtifactUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            artifact=Artifact(artifact_id="a1", parts=[]),
            last_chunk=True,
        )
        events = agent._events_from_response(artifact_event, 1, ctx)
        assert len(events) == 0

    def test_status_event_with_agent_message(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = TaskStatusUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            final=False,
            status=TaskStatus(
                state=TaskState.input_required,
                message=Message(
                    message_id="m1",
                    role=Role.agent,
                    parts=[A2APart(root=TextPart(text="need input"))],
                ),
            ),
        )
        events = agent._events_from_response(status_event, 1, ctx)
        assert len(events) == 1

    def test_status_event_user_message_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = TaskStatusUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            final=False,
            status=TaskStatus(
                state=TaskState.working,
                message=Message(
                    message_id="m1",
                    role=Role.user,
                    parts=[A2APart(root=TextPart(text="user msg"))],
                ),
            ),
        )
        events = agent._events_from_response(status_event, 1, ctx)
        assert len(events) == 0

    def test_status_event_no_message_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = TaskStatusUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            final=False,
            status=TaskStatus(state=TaskState.working),
        )
        events = agent._events_from_response(status_event, 1, ctx)
        assert len(events) == 0

    def test_status_working_state_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = TaskStatusUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            final=False,
            status=TaskStatus(
                state=TaskState.working,
                message=Message(
                    message_id="m1",
                    role=Role.agent,
                    parts=[A2APart(root=TextPart(text="working"))],
                ),
            ),
        )
        events = agent._events_from_response(status_event, 1, ctx)
        assert len(events) == 0

    def test_task_result(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        task = Task(
            id="t1",
            context_id="ctx1",
            status=TaskStatus(
                state=TaskState.completed,
                message=Message(
                    message_id="m1",
                    role=Role.agent,
                    parts=[A2APart(root=TextPart(text="done"))],
                ),
            ),
        )
        events = agent._events_from_response(task, 1, ctx)
        assert len(events) == 1

    def test_message_result(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        msg = Message(
            message_id="m1",
            role=Role.agent,
            parts=[A2APart(root=TextPart(text="hello"))],
        )
        events = agent._events_from_response(msg, 1, ctx)
        assert len(events) == 1

    def test_unknown_result(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        events = agent._events_from_response("unknown_object", 1, ctx)
        assert len(events) == 1
        assert "unknown" in events[0].content.parts[0].text.lower()

    def test_artifact_with_streaming_tool_call_metadata(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        artifact_event = TaskArtifactUpdateEvent(
            task_id="t1",
            context_id="ctx1",
            artifact=Artifact(
                artifact_id="a1",
                parts=[A2APart(root=TextPart(text="result"))],
            ),
            last_chunk=False,
            metadata={"streaming_tool_call": "true"},
        )
        events = agent._events_from_response(artifact_event, 1, ctx)
        assert len(events) == 1
        assert events[0].partial is True


# ---------------------------------------------------------------------------
# _run_async_impl
# ---------------------------------------------------------------------------
class TestRunAsyncImpl:
    async def test_not_initialized_yields_error_event(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._initialized = False
        ctx = _make_invocation_context()
        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)
        assert len(events) == 1
        assert "not initialized" in events[0].error_message

    async def test_no_message_yields_empty_event(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._initialized = True
        ctx = _make_invocation_context(override_messages=None, events=[])
        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)
        assert len(events) == 1
        assert events[0].content is not None
