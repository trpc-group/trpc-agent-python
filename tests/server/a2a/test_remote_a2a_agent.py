# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a._remote_a2a_agent."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    Message,
    Part as A2APart,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
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
        version="1.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url="http://remote:8080"),
        ],
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


def _artifact_event(**overrides):
    return TaskArtifactUpdateEvent(
        task_id=overrides.get("task_id", "t1"),
        context_id=overrides.get("context_id", "ctx1"),
        artifact=overrides.get(
            "artifact",
            Artifact(artifact_id="a1", parts=[A2APart(text="result")]),
        ),
        last_chunk=overrides.get("last_chunk", False),
        metadata=overrides.get("metadata"),
    )


def _status_event(state: TaskState, **overrides):
    return TaskStatusUpdateEvent(
        task_id=overrides.get("task_id", "t1"),
        context_id=overrides.get("context_id", "ctx1"),
        status=overrides.get(
            "status",
            TaskStatus(
                state=state,
                message=overrides.get(
                    "message",
                    Message(message_id="m1", role=Role.ROLE_AGENT, parts=[A2APart(text="msg")]),
                ),
            ),
        ),
    )


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

    async def test_injected_client_skips_card_and_httpx(self):
        client = MagicMock()
        agent = TrpcRemoteA2aAgent(name="remote", a2a_client=client)
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.A2ACardResolver") as MockResolver, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client") as MockCreateClient, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.httpx.AsyncClient") as MockHttpx:
            result = await agent.initialize()
        assert result is True
        assert agent._initialized is True
        assert agent._a2a_client is client
        assert agent._agent_card is None
        assert agent._httpx_client is None
        MockResolver.assert_not_called()
        MockCreateClient.assert_not_called()
        MockHttpx.assert_not_called()

    async def test_compat_injected_client_does_not_require_url(self):
        client = MagicMock()
        agent = TrpcRemoteA2aAgent(
            name="remote",
            a2a_client=client,
            enable_v0_3_compat=True,
        )
        result = await agent.initialize()
        assert result is True
        assert agent._a2a_client is client
        assert agent._httpx_client is None

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
        # No card provided -> discover via A2ACardResolver, then build the
        # client from the discovered card.
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.A2ACardResolver") as MockResolver, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client", new_callable=AsyncMock) as MockCreateClient:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=mock_card)
            MockCreateClient.return_value = MagicMock()
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://remote:8080")
            result = await agent.initialize()
            assert result is True
            MockCreateClient.assert_awaited_once()
            assert MockCreateClient.call_args.args[0] is mock_card
            assert agent._agent_card is mock_card
            if agent._httpx_client:
                await agent._httpx_client.aclose()

    async def test_failure_returns_false(self):
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client",
                   side_effect=Exception("connection failed")):
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

    async def test_explicit_v03_uses_create_client_auto_negotiation(self):
        # enable_v0_3_compat=True prefers a2a-sdk's automatic negotiation:
        # when a card resolves, create_client() selects the transport by
        # protocol_version (JsonRpcTransport for 1.0, CompatJsonRpcTransport
        # for v0.3).  Assert it goes through create_client with the resolved card.
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.TrpcRemoteA2aAgent._resolve_legacy_card",
                   new=AsyncMock(return_value=mock_card)) as MockResolve, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client", new_callable=AsyncMock) as MockCreateClient:
            MockCreateClient.return_value = MagicMock()
            agent = TrpcRemoteA2aAgent(
                name="remote",
                agent_base_url="http://127.0.0.1:18081",
                enable_v0_3_compat=True,
            )
            result = await agent.initialize()
        assert result is True
        MockResolve.assert_awaited_once()
        MockCreateClient.assert_awaited_once()
        assert MockCreateClient.call_args.args[0] is mock_card
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_explicit_v03_without_base_url_raises(self):
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_base_url="http://x",
            enable_v0_3_compat=True,
        )
        agent.agent_base_url = None
        result = await agent.initialize()
        assert result is False
        assert agent._a2a_client is None
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_explicit_v03_empty_interfaces_skips_negotiation(self):
        # A pure v0.3 server's card has no supportedInterfaces, so create_client()
        # would fail to negotiate; compat must short-circuit straight to the
        # CompatJsonRpcTransport wire without calling create_client.
        empty_card = _make_agent_card()
        empty_card.ClearField("supported_interfaces")
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_base_url="http://127.0.0.1:18081",
            enable_v0_3_compat=True,
        )
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.TrpcRemoteA2aAgent._resolve_legacy_card",
                   new=AsyncMock(return_value=empty_card)) as MockResolve, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client", new_callable=AsyncMock) as MockCreateClient:
            result = await agent.initialize()
        assert result is True
        MockResolve.assert_awaited_once()
        MockCreateClient.assert_not_awaited()
        assert type(agent._a2a_client._transport).__name__ == "CompatJsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://127.0.0.1:18081"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_explicit_v03_empty_interface_url_skips_negotiation(self):
        # A pure v0.3 server's card has an interface whose url is "" (the 0.3
        # layout leaves the address to the client).  create_client would build a
        # transport with an empty url and fail at request time, so compat must
        # fall back to the CompatJsonRpcTransport wire posting to agent_base_url.
        card = _make_agent_card()
        card.supported_interfaces[0].url = ""
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_base_url="http://127.0.0.1:18081",
            enable_v0_3_compat=True,
        )
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.TrpcRemoteA2aAgent._resolve_legacy_card",
                   new=AsyncMock(return_value=card)) as MockResolve, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client", new_callable=AsyncMock) as MockCreateClient:
            result = await agent.initialize()
        assert result is True
        MockResolve.assert_awaited_once()
        MockCreateClient.assert_not_awaited()
        assert type(agent._a2a_client._transport).__name__ == "CompatJsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://127.0.0.1:18081"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_explicit_v03_no_card_falls_back_to_compat(self):
        # When the card cannot be resolved (None), there is nothing to
        # negotiate; compat falls back to the CompatJsonRpcTransport wire.
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_base_url="http://127.0.0.1:18081",
            enable_v0_3_compat=True,
        )
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.TrpcRemoteA2aAgent._resolve_legacy_card",
                   new=AsyncMock(return_value=None)) as MockResolve, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client", new_callable=AsyncMock) as MockCreateClient:
            result = await agent.initialize()
        assert result is True
        MockResolve.assert_awaited_once()
        MockCreateClient.assert_not_awaited()
        assert type(agent._a2a_client._transport).__name__ == "CompatJsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://127.0.0.1:18081"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_default_version_still_uses_create_client(self):
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.A2ACardResolver") as MockResolver, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.create_client", new_callable=AsyncMock) as MockCreateClient:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=mock_card)
            MockCreateClient.return_value = MagicMock()
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://remote:8080")
            result = await agent.initialize()
        assert result is True
        MockCreateClient.assert_awaited_once()
        assert MockCreateClient.call_args.args[0] is mock_card
        if agent._httpx_client:
            await agent._httpx_client.aclose()


# ---------------------------------------------------------------------------
# _resolve_legacy_card
# ---------------------------------------------------------------------------
class TestResolveLegacyCard:
    def _agent(self, url="http://127.0.0.1:18081"):
        agent = TrpcRemoteA2aAgent(name="remote", agent_base_url=url, enable_v0_3_compat=True)
        agent._httpx_client = AsyncMock()
        return agent

    async def test_parses_v03_card_and_converts_to_v10(self):
        # A pure v0.3 server serves a top-level-url card; it must be parsed as a
        # 0.3 card and converted to the 1.x protobuf form (url/capabilities kept).
        agent = self._agent()
        agent._httpx_client.get.return_value = AsyncMock(
            status_code=200,
            json=lambda: {
                "name": "weather",
                "description": "Weather agent",
                "version": "0.0.1",
                "url": "http://127.0.0.1:18081",
                "preferredTransport": "JSONRPC",
                "capabilities": {"streaming": True},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
            raise_for_status=lambda: None,
        )
        card = await agent._resolve_legacy_card()
        assert card is not None
        assert card.name == "weather"
        assert card.description == "Weather agent"
        assert card.capabilities.streaming is True
        interfaces = [
            (i.protocol_binding, i.protocol_version, i.url)
            for i in card.supported_interfaces
        ]
        assert interfaces == [("JSONRPC", "0.3.0", "http://127.0.0.1:18081")]

    async def test_falls_back_to_v10_resolver_when_card_is_v10_layout(self):
        # A 1.x server running with compat serves a 1.x-layout card; the 0.3
        # parse fails and we fall back to the 1.x resolver.
        agent = self._agent()
        # Raw body fails 0.3 validation, so the v0.3 branch is skipped.
        agent._httpx_client.get.return_value = AsyncMock(
            status_code=200,
            json=lambda: {"name": "x"},  # not a valid 0.3 card
            raise_for_status=lambda: None,
        )
        v10_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=v10_card)
            card = await agent._resolve_legacy_card()
        assert card is v10_card

    async def test_http_failure_returns_none(self):
        # The v0.3 wire can still work without a card; a failed fetch must not
        # propagate.
        agent = self._agent()
        agent._httpx_client.get.side_effect = httpx.ConnectError("boom")
        card = await agent._resolve_legacy_card()
        assert card is None

    async def test_unparseable_card_warns_and_returns_none(self):
        # The card was fetched but is neither 0.3 nor 1.0 layout: warn (it can
        # mask a compatibility issue) but still continue without a card.
        agent = self._agent()
        agent._httpx_client.get.return_value = AsyncMock(
            status_code=200,
            json=lambda: {"unexpected": "shape"},
            raise_for_status=lambda: None,
        )
        with patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.logger.warning") as MockWarn, \
             patch("trpc_agent_sdk.server.a2a._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(side_effect=ValueError("bad"))
            card = await agent._resolve_legacy_card()
        assert card is None
        MockWarn.assert_called_once()
        assert "could not be parsed" in MockWarn.call_args.args[0]


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
        event = _artifact_event()
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        msg = agent._build_message_from_artifact_event(event)
        assert msg.role == Role.ROLE_AGENT
        assert len(msg.parts) == 1

    def test_without_artifact(self):
        event = MagicMock()
        event.artifact = None
        delattr(event, "artifact")
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        msg = agent._build_message_from_artifact_event(event)
        assert msg.role == Role.ROLE_AGENT
        assert len(msg.parts) == 0


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
# _response_payload
# ---------------------------------------------------------------------------
class TestResponsePayload:
    def test_task_payload(self):
        task = Task(id="t1", context_id="ctx1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        from a2a.types import StreamResponse
        resp = StreamResponse(task=task)
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._response_payload(resp) == task

    def test_message_payload(self):
        from a2a.types import StreamResponse
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[A2APart(text="hi")])
        resp = StreamResponse(message=msg)
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._response_payload(resp) == msg

    def test_status_update_payload(self):
        from a2a.types import StreamResponse
        status = _status_event(TaskState.TASK_STATE_WORKING)
        resp = StreamResponse(status_update=status)
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._response_payload(resp) == status

    def test_artifact_update_payload(self):
        from a2a.types import StreamResponse
        artifact = _artifact_event()
        resp = StreamResponse(artifact_update=artifact)
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._response_payload(resp) == artifact


# ---------------------------------------------------------------------------
# _events_from_response
# ---------------------------------------------------------------------------
class TestEventsFromResponse:
    def _make_agent(self):
        return TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())

    def test_artifact_event_with_parts(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        events = agent._events_from_response(_artifact_event(), 1, ctx)
        assert len(events) == 1

    def test_artifact_event_empty_last_chunk_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        artifact_event = _artifact_event(
            artifact=Artifact(artifact_id="a1", parts=[]),
            last_chunk=True,
        )
        events = agent._events_from_response(artifact_event, 1, ctx)
        assert len(events) == 0

    def test_status_event_with_agent_message(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = _status_event(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            message=Message(message_id="m1", role=Role.ROLE_AGENT, parts=[A2APart(text="need input")]),
        )
        events = agent._events_from_response(status_event, 1, ctx)
        assert len(events) == 1

    def test_status_event_user_message_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = _status_event(
            TaskState.TASK_STATE_WORKING,
            message=Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="user msg")]),
        )
        events = agent._events_from_response(status_event, 1, ctx)
        assert len(events) == 0

    def test_status_event_no_message_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = _status_event(
            TaskState.TASK_STATE_WORKING,
            message=None,
        )
        events = agent._events_from_response(status_event, 1, ctx)
        assert len(events) == 0

    def test_status_working_state_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        status_event = _status_event(
            TaskState.TASK_STATE_WORKING,
            message=Message(message_id="m1", role=Role.ROLE_AGENT, parts=[A2APart(text="working")]),
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
                state=TaskState.TASK_STATE_COMPLETED,
                message=Message(message_id="m1", role=Role.ROLE_AGENT, parts=[A2APart(text="done")]),
            ),
        )
        events = agent._events_from_response(task, 1, ctx)
        assert len(events) == 1

    def test_message_result(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        msg = Message(message_id="m1", role=Role.ROLE_AGENT, parts=[A2APart(text="hello")])
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
        artifact_event = _artifact_event(metadata={"streaming_tool_call": "true"})
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
