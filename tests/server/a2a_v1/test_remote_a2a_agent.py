# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a_v1._remote_a2a_agent."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    Message,
    Part as A2APart,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.server.a2a_v1._remote_a2a_agent import TrpcRemoteA2aAgent
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
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver, \
             patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.create_client") as MockCreateClient, \
             patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.httpx.AsyncClient") as MockHttpx:
            result = await agent.initialize()
        assert result is True
        assert agent._initialized is True
        assert agent._a2a_client is client
        assert agent._agent_card is None
        assert agent._httpx_client is None
        MockResolver.assert_not_called()
        MockCreateClient.assert_not_called()
        MockHttpx.assert_not_called()

    async def test_legacy_injected_client_does_not_require_url(self):
        client = MagicMock()
        agent = TrpcRemoteA2aAgent(
            name="remote",
            a2a_client=client,
            force_v0_3=True,
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

    async def test_httpx_client_sets_connect_and_read_timeouts(self):
        card = _make_agent_card()
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=card, agent_base_url="http://x")
        try:
            assert await agent.initialize() is True
            timeout = agent._httpx_client.timeout
            assert timeout.connect == 5.0
            assert timeout.read == 600.0
            assert timeout.write == 600.0
            assert timeout.pool == 600.0
        finally:
            if agent._httpx_client:
                await agent._httpx_client.aclose()

    async def test_card_discovery_passes_finite_request_timeout(self):
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=mock_card)
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://remote:8080")
            try:
                assert await agent.initialize() is True
                call_kwargs = MockResolver.return_value.get_agent_card.call_args.kwargs
                timeout = call_kwargs["http_kwargs"]["timeout"]
                assert timeout.connect == 5.0
                assert timeout.read == 30.0
            finally:
                if agent._httpx_client:
                    await agent._httpx_client.aclose()

    async def test_without_card_resolves(self):
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=mock_card)
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://remote:8080")
            result = await agent.initialize()
            assert result is True
            assert agent._agent_card is mock_card
            assert type(agent._a2a_client._transport).__name__ == "JsonRpcTransport"
            assert agent._a2a_client._transport.url == "http://remote:8080"
            if agent._httpx_client:
                await agent._httpx_client.aclose()

    async def test_failure_returns_false(self):
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(side_effect=Exception("connection failed"))
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://bad:1234")
            result = await agent.initialize()
            assert result is False
            assert agent._initialized is False
            assert agent._httpx_client is None

    async def test_failure_closes_owned_httpx_client(self):
        created_clients = []

        def _make_client(*_args, **_kwargs):
            client = MagicMock()
            client.aclose = AsyncMock()
            created_clients.append(client)
            return client

        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver, \
             patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.httpx.AsyncClient", side_effect=_make_client):
            MockResolver.return_value.get_agent_card = AsyncMock(side_effect=Exception("connection failed"))
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://bad:1234")
            assert await agent.initialize() is False
            assert await agent.initialize() is False

        assert len(created_clients) == 2
        for client in created_clients:
            client.aclose.assert_awaited()
        assert agent._httpx_client is None
        assert agent._initialized is False

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

    async def test_force_v0_3_uses_compat_wire_even_for_1_0_card(self):
        # force_v0_3=True means the peer is 0.3.  A 1.0-shaped card must not
        # switch the client onto JsonRpcTransport / create_client negotiation.
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=mock_card)
            agent = TrpcRemoteA2aAgent(
                name="remote",
                agent_base_url="http://127.0.0.1:18081",
                force_v0_3=True,
            )
            result = await agent.initialize()
        assert result is True
        MockResolver.assert_called_once()
        assert type(agent._a2a_client._transport).__name__ == "CompatJsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://remote:8080"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_force_v0_3_without_base_url_raises(self):
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_base_url="http://x",
            force_v0_3=True,
        )
        agent.agent_base_url = None
        result = await agent.initialize()
        assert result is False
        assert agent._a2a_client is None
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_build_v0_3_client_requires_url(self):
        # Card with no JSONRPC url and no agent_base_url: the raise at
        # _build_v0_3_a2a_client must fire (initialize() would swallow it).
        card = _make_agent_card()
        card.ClearField("supported_interfaces")
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=card, force_v0_3=True)
        with pytest.raises(ValueError, match="agent_base_url is required for force_v0_3"):
            await agent._build_v0_3_a2a_client()
        empty_card = _make_agent_card()
        empty_card.ClearField("supported_interfaces")
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_base_url="http://127.0.0.1:18081",
            force_v0_3=True,
        )
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=empty_card)
            result = await agent.initialize()
        assert result is True
        assert list(empty_card.supported_interfaces) == []
        assert type(agent._a2a_client._transport).__name__ == "CompatJsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://127.0.0.1:18081"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_force_v0_3_empty_interface_url_uses_agent_base_url(self):
        card = _make_agent_card()
        card.supported_interfaces[0].url = ""
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_base_url="http://127.0.0.1:18081",
            force_v0_3=True,
        )
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=card)
            result = await agent.initialize()
        assert result is True
        assert type(agent._a2a_client._transport).__name__ == "CompatJsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://127.0.0.1:18081"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_force_v0_3_discovery_failure_returns_false(self):
        # Both flags require a card.  A failed fetch must not fall back to
        # posting 0.3 JSON-RPC at agent_base_url with no AgentCard.
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(side_effect=Exception("connection failed"))
            agent = TrpcRemoteA2aAgent(
                name="remote",
                agent_base_url="http://127.0.0.1:18081",
                force_v0_3=True,
            )
            result = await agent.initialize()
        assert result is False
        assert agent._a2a_client is None
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_default_uses_1_0_jsonrpc_transport(self):
        mock_card = _make_agent_card()
        with patch("trpc_agent_sdk.server.a2a_v1._remote_a2a_agent.A2ACardResolver") as MockResolver:
            MockResolver.return_value.get_agent_card = AsyncMock(return_value=mock_card)
            agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://remote:8080")
            result = await agent.initialize()
        assert result is True
        assert type(agent._a2a_client._transport).__name__ == "JsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://remote:8080"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_default_create_client_follows_card_protocol_version(self):
        # Default path leaves transport selection to create_client: a JSONRPC
        # interface with protocol_version=0.3 uses CompatJsonRpcTransport.
        card = _make_agent_card()
        card.supported_interfaces[0].protocol_version = "0.3"
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_card=card,
            agent_base_url="http://127.0.0.1:18081",
        )
        result = await agent.initialize()
        assert result is True
        assert type(agent._a2a_client._transport).__name__ == "CompatJsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://remote:8080"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_empty_jsonrpc_url_filled_from_agent_base_url(self):
        card = _make_agent_card()
        card.supported_interfaces[0].url = ""
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_card=card,
            agent_base_url="http://127.0.0.1:18081",
        )
        result = await agent.initialize()
        assert result is True
        assert card.supported_interfaces[0].url == ""
        assert agent._agent_card.supported_interfaces[0].protocol_binding == "JSONRPC"
        assert agent._agent_card.supported_interfaces[0].url == "http://127.0.0.1:18081"
        assert type(agent._a2a_client._transport).__name__ == "JsonRpcTransport"
        assert agent._a2a_client._transport.url == "http://127.0.0.1:18081"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_non_empty_jsonrpc_url_not_overwritten(self):
        card = _make_agent_card()
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_card=card,
            agent_base_url="http://127.0.0.1:18081",
        )
        result = await agent.initialize()
        assert result is True
        assert card.supported_interfaces[0].url == "http://remote:8080"
        assert agent._a2a_client._transport.url == "http://remote:8080"
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_empty_grpc_url_not_filled(self):
        card = _make_agent_card()
        card.supported_interfaces[0].url = ""
        card.supported_interfaces.append(
            AgentInterface(protocol_binding="GRPC", protocol_version="1.0", url=""),
        )
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_card=card,
            agent_base_url="http://127.0.0.1:18081",
        )
        result = await agent.initialize()
        assert result is True
        assert {i.protocol_binding: i.url for i in card.supported_interfaces} == {
            "JSONRPC": "",
            "GRPC": "",
        }
        urls_by_binding = {i.protocol_binding: i.url for i in agent._agent_card.supported_interfaces}
        assert urls_by_binding["JSONRPC"] == "http://127.0.0.1:18081"
        assert urls_by_binding["GRPC"] == ""
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_empty_interfaces_default_does_not_synthesize_jsonrpc(self):
        # A 0.3 card with empty top-level url parses to no interfaces.  Default
        # does not invent a 0.3 JSONRPC binding; create_client has nothing to
        # connect with.  Use force_v0_3=True to post at agent_base_url.
        empty_card = _make_agent_card()
        empty_card.ClearField("supported_interfaces")
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_card=empty_card,
            agent_base_url="http://127.0.0.1:18081",
        )
        result = await agent.initialize()
        assert result is False
        assert list(empty_card.supported_interfaces) == []
        assert agent._a2a_client is None
        if agent._httpx_client:
            await agent._httpx_client.aclose()

    async def test_grpc_only_card_does_not_synthesize_jsonrpc(self):
        card = _make_agent_card()
        card.ClearField("supported_interfaces")
        card.supported_interfaces.append(
            AgentInterface(protocol_binding="GRPC", protocol_version="1.0", url=""),
        )
        agent = TrpcRemoteA2aAgent(
            name="remote",
            agent_card=card,
            agent_base_url="http://127.0.0.1:18081",
        )
        agent._fill_empty_jsonrpc_urls()
        assert [i.protocol_binding for i in card.supported_interfaces] == ["GRPC"]
        assert card.supported_interfaces[0].url == ""

    def test_fill_empty_jsonrpc_urls_skips_without_base_url(self):
        card = _make_agent_card()
        card.supported_interfaces[0].url = ""
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=card)
        agent._fill_empty_jsonrpc_urls()
        assert card.supported_interfaces[0].url == ""

    def test_fill_empty_jsonrpc_urls_does_not_mutate_input_card(self):
        card = _make_agent_card()
        card.supported_interfaces[0].url = ""
        agent_a = TrpcRemoteA2aAgent(
            name="a",
            agent_card=card,
            agent_base_url="http://agent-a:8080",
        )
        agent_b = TrpcRemoteA2aAgent(
            name="b",
            agent_card=card,
            agent_base_url="http://agent-b:8080",
        )
        agent_a._fill_empty_jsonrpc_urls()
        agent_b._fill_empty_jsonrpc_urls()
        assert card.supported_interfaces[0].url == ""
        assert agent_a._agent_card is not card
        assert agent_b._agent_card is not card
        assert agent_a._agent_card.supported_interfaces[0].url == "http://agent-a:8080"
        assert agent_b._agent_card.supported_interfaces[0].url == "http://agent-b:8080"

    def test_first_jsonrpc_url_none_when_card_missing(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_base_url="http://x")
        assert agent._agent_card is None
        assert agent._first_jsonrpc_url() is None


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

    def test_empty_payload_returns_none(self):
        resp = StreamResponse()
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        assert agent._response_payload(resp) is None


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

    def test_none_result_skipped(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        assert agent._events_from_response(None, 1, ctx) == []

    def test_artifact_with_streaming_tool_call_metadata(self):
        agent = self._make_agent()
        ctx = _make_invocation_context()
        artifact_event = _artifact_event(metadata={"streaming_tool_call": "true"})
        events = agent._events_from_response(artifact_event, 1, ctx)
        assert len(events) == 1
        assert events[0].partial is True


# ---------------------------------------------------------------------------
# _task_id_from_payload
# ---------------------------------------------------------------------------
class TestTaskIdFromPayload:
    def _make_agent(self):
        return TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())

    def test_task_uses_id(self):
        # 1.x Task has `id`, not `task_id`. The initial stream event is a Task.
        agent = self._make_agent()
        task = Task(id="task-from-id", context_id="ctx1", status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
        assert agent._task_id_from_payload(task) == "task-from-id"

    def test_status_update_uses_task_id(self):
        agent = self._make_agent()
        assert agent._task_id_from_payload(_status_event(TaskState.TASK_STATE_WORKING)) == "t1"

    def test_artifact_update_uses_task_id(self):
        agent = self._make_agent()
        assert agent._task_id_from_payload(_artifact_event()) == "t1"

    def test_unknown_payload_returns_none(self):
        agent = self._make_agent()
        assert agent._task_id_from_payload("not-a-payload") is None


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

    async def test_merges_existing_message_metadata(self):
        from google.protobuf.json_format import MessageToDict

        outgoing = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        outgoing.metadata.update({
            "custom_key": "custom_val",
            "nested": {"a": [1, 2, 3]},
            "nullable": None,
        })

        async def empty_stream():
            if False:
                yield StreamResponse()

        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._initialized = True
        agent._a2a_client = MagicMock()
        agent._a2a_client.send_message = MagicMock(return_value=empty_stream())
        ctx = _make_invocation_context()

        with patch.object(agent, "_build_outgoing_message", return_value=outgoing):
            async for _ in agent._run_async_impl(ctx):
                pass

        request = agent._a2a_client.send_message.call_args.args[0]
        merged = MessageToDict(request.message.metadata)
        # Framework keys are filled only when absent (same as 0.3).
        assert merged["custom_key"] == "custom_val"
        assert merged["nested"] == {"a": [1.0, 2.0, 3.0]}
        assert merged["user_id"] == "user-1"
        assert "nullable" in request.message.metadata
        assert request.message.metadata["nullable"] is None

    async def test_existing_user_id_is_not_overwritten_by_context(self):
        from google.protobuf.json_format import MessageToDict

        outgoing = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        outgoing.metadata.update({"user_id": "biz-user", "custom_key": "keep-me"})

        async def empty_stream():
            if False:
                yield StreamResponse()

        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._initialized = True
        agent._a2a_client = MagicMock()
        agent._a2a_client.send_message = MagicMock(return_value=empty_stream())
        ctx = _make_invocation_context(user_id="user-1")

        with patch.object(agent, "_build_outgoing_message", return_value=outgoing):
            async for _ in agent._run_async_impl(ctx):
                pass

        request = agent._a2a_client.send_message.call_args.args[0]
        merged = MessageToDict(request.message.metadata)
        assert merged["user_id"] == "biz-user"
        assert merged["custom_key"] == "keep-me"
        assert merged["invocation_id"] == "inv-1"

    async def test_empty_stream_response_is_skipped(self):
        outgoing = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])

        async def stream():
            yield StreamResponse()

        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._initialized = True
        agent._a2a_client = MagicMock()
        agent._a2a_client.send_message = MagicMock(return_value=stream())
        ctx = _make_invocation_context()

        events = []
        with patch.object(agent, "_build_outgoing_message", return_value=outgoing):
            async for event in agent._run_async_impl(ctx):
                events.append(event)

        assert events == []

    async def test_cancel_uses_id_from_initial_task(self):
        # 1.x streams Task first (field `id`). If cancel arrives before any
        # TaskStatusUpdateEvent/TaskArtifactUpdateEvent, cancel must still use
        # that id — not depend on `task_id`.
        from google.genai import types as genai_types

        task = Task(
            id="task-from-id",
            context_id="ctx1",
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )

        async def stream():
            yield StreamResponse(task=task)
            raise RunCancelledException("cancelled")

        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        agent._initialized = True
        agent._a2a_client = MagicMock()
        agent._a2a_client.send_message = MagicMock(return_value=stream())
        agent._a2a_client.cancel_task = AsyncMock()
        ctx = _make_invocation_context(
            override_messages=[genai_types.Content(role="user", parts=[genai_types.Part(text="hi")])]
        )

        with pytest.raises(RunCancelledException):
            async for _ in agent._run_async_impl(ctx):
                pass

        agent._a2a_client.cancel_task.assert_awaited()
        cancel_request = agent._a2a_client.cancel_task.call_args.args[0]
        assert cancel_request.id == "task-from-id"


# ---------------------------------------------------------------------------
# _stream_with_cancel_check
# ---------------------------------------------------------------------------
class _HangingStream:
    """Async iterator that only releases the HTTP-like resource in aclose()."""

    def __init__(self):
        self.closed = False
        self._hang = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._hang.wait()
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


class TestStreamWithCancelCheck:
    async def test_cancel_closes_inner_stream_iterator(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        cancel_event = asyncio.Event()
        cancel_event.set()
        ctx = _make_invocation_context()
        ctx.get_cancel_event = AsyncMock(return_value=cancel_event)
        stream = _HangingStream()

        with pytest.raises(RunCancelledException, match="waiting for stream response"):
            async for _ in agent._stream_with_cancel_check(ctx, stream):
                pass

        assert stream.closed is True

    async def test_wrapper_cancellation_closes_inner_stream_iterator(self):
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        ctx = _make_invocation_context()
        ctx.get_cancel_event = AsyncMock(return_value=asyncio.Event())
        stream = _HangingStream()

        async def consume():
            async for _ in agent._stream_with_cancel_check(ctx, stream):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert stream.closed is True

    async def test_simultaneous_cancel_and_frame_absorbs_next_response_task(self):
        # FIRST_COMPLETED can put both tasks in done. Cancel still wins, but the
        # completed __anext__ task must be retrieved or asyncio logs
        # "Task exception was never retrieved" (StopAsyncIteration).
        agent = TrpcRemoteA2aAgent(name="remote", agent_card=_make_agent_card())
        cancel_event = asyncio.Event()
        cancel_event.set()
        ctx = _make_invocation_context()
        ctx.get_cancel_event = AsyncMock(return_value=cancel_event)

        class _ImmediateStop:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self):
                pass

        orig_wait = asyncio.wait

        async def wait_both(aws, **kwargs):
            return await orig_wait(aws, return_when=asyncio.ALL_COMPLETED)

        recorded = []
        orig_create_task = asyncio.create_task

        def tracking_create_task(coro, *args, **kwargs):
            task = orig_create_task(coro, *args, **kwargs)
            recorded.append(task)
            return task

        with patch.object(asyncio, "wait", side_effect=wait_both), \
             patch.object(asyncio, "create_task", side_effect=tracking_create_task):
            with pytest.raises(RunCancelledException, match="waiting for stream response"):
                async for _ in agent._stream_with_cancel_check(ctx, _ImmediateStop()):
                    pass

        assert recorded
        assert all(task.done() for task in recorded)
        assert all(not getattr(task, "_log_traceback", False) for task in recorded)
