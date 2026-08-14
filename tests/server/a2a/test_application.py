# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a._application."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface

from trpc_agent_sdk.server.a2a._application import _ensure_v0_3_interface
from trpc_agent_sdk.server.a2a._application import _jsonrpc_path_from_card
from trpc_agent_sdk.server.a2a._application import create_a2a_application


def _make_card(url: str = ""):
    return AgentCard(
        name="svc",
        description="Test agent",
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=url),
        ],
        skills=[],
    )


def _make_service(card: AgentCard):
    svc = MagicMock()
    svc.agent_card = card
    return svc


# ---------------------------------------------------------------------------
# _ensure_v0_3_interface
# ---------------------------------------------------------------------------
class TestEnsureV03Interface:
    def test_adds_v03_interface(self):
        card = _make_card(url="http://host:18081/")
        _ensure_v0_3_interface(card)
        versions = [
            (i.protocol_binding, i.protocol_version, i.url)
            for i in card.supported_interfaces
        ]
        assert ("JSONRPC", "1.0", "http://host:18081/") in versions
        assert ("JSONRPC", "0.3", "http://host:18081/") in versions

    def test_does_not_duplicate_v03_interface(self):
        card = _make_card(url="http://host:18081/")
        _ensure_v0_3_interface(card)
        _ensure_v0_3_interface(card)
        count = sum(
            1
            for i in card.supported_interfaces
            if i.protocol_binding == "JSONRPC" and i.protocol_version == "0.3"
        )
        assert count == 1

    def test_v03_interface_reuses_existing_url(self):
        # The appended 0.3 interface must point at the advertised url.
        card = _make_card(url="https://agent.example.com/a2a")
        _ensure_v0_3_interface(card)
        for i in card.supported_interfaces:
            if i.protocol_version == "0.3":
                assert i.url == "https://agent.example.com/a2a"


# ---------------------------------------------------------------------------
# _jsonrpc_path_from_card
# ---------------------------------------------------------------------------
class TestJsonrpcPathFromCard:
    def test_bare_origin_defaults_to_root(self):
        assert _jsonrpc_path_from_card(_make_card(url="http://host:18081")) == "/"

    def test_path_url(self):
        assert _jsonrpc_path_from_card(_make_card(url="https://agent.example.com/a2a")) == "/a2a"

    def test_path_with_trailing_slash(self):
        assert _jsonrpc_path_from_card(_make_card(url="https://agent.example.com/a2a/")) == "/a2a/"

    def test_empty_url_defaults_to_root(self):
        assert _jsonrpc_path_from_card(_make_card(url="")) == "/"

    def test_prefers_10_interface_url(self):
        # A framework-built card has a single interface; the first JSONRPC url
        # advertised is the one clients discover and the mount must follow it.
        card = _make_card(url="")
        card.ClearField("supported_interfaces")
        card.supported_interfaces.extend([
            AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url="https://x.com/a2a"),
            AgentInterface(protocol_binding="JSONRPC", protocol_version="0.3", url="https://x.com/legacy"),
        ])
        assert _jsonrpc_path_from_card(card) == "/a2a"

    def test_falls_back_when_10_has_no_url(self):
        # 1.0 interface empty -> fall back to any advertised url.
        card = _make_card(url="")
        card.ClearField("supported_interfaces")
        card.supported_interfaces.extend([
            AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=""),
            AgentInterface(protocol_binding="JSONRPC", protocol_version="0.3", url="https://x.com/legacy"),
        ])
        assert _jsonrpc_path_from_card(card) == "/legacy"


# ---------------------------------------------------------------------------
# create_a2a_application
# ---------------------------------------------------------------------------
class TestCreateA2aApplication:
    def test_default_builds_default_handler(self):
        svc = _make_service(_make_card(url="http://host:18081"))

        with patch("trpc_agent_sdk.server.a2a._application.DefaultRequestHandler") as MockHandler:
            create_a2a_application(svc)
        call_kwargs = MockHandler.call_args.kwargs
        assert call_kwargs["agent_executor"] is svc
        assert isinstance(call_kwargs["task_store"], InMemoryTaskStore)
        assert call_kwargs["agent_card"] is svc.agent_card

    def test_uses_custom_request_handler(self):
        custom_handler = MagicMock()
        svc = _make_service(_make_card(url="http://host:18081"))

        with patch("trpc_agent_sdk.server.a2a._application.DefaultRequestHandler") as MockHandler:
            app = create_a2a_application(svc, request_handler=custom_handler)
        # The provided handler must be used as-is; DefaultRequestHandler is not
        # constructed.
        MockHandler.assert_not_called()
        assert app is not None

    def test_missing_url_warns_but_starts(self):
        # No rpc_url configured anywhere: the server must still start (JSON-RPC
        # direct callers don't read the card), but a warning points out the
        # card is undiscoverable.
        svc = _make_service(_make_card(url=""))
        with patch("trpc_agent_sdk.server.a2a._application.logger.warning") as mock_warn:
            app = create_a2a_application(svc)
        assert app is not None
        mock_warn.assert_called_once()
        assert "no reachable url" in mock_warn.call_args.args[0]

    def test_ok_when_card_has_url(self):
        svc = _make_service(_make_card(url="http://host:18081"))
        app = create_a2a_application(svc)
        assert app is not None

    def test_compat_adds_v03_interface(self):
        svc = _make_service(_make_card(url="http://host:18081"))
        create_a2a_application(svc, enable_v0_3_compat=True)
        versions = [
            (i.protocol_binding, i.protocol_version)
            for i in svc.agent_card.supported_interfaces
        ]
        assert ("JSONRPC", "0.3") in versions

    def test_compat_with_missing_url_warns_but_starts(self):
        # Compat must not paper over a missing url, but must not block startup.
        svc = _make_service(_make_card(url=""))
        with patch("trpc_agent_sdk.server.a2a._application.logger.warning") as mock_warn:
            app = create_a2a_application(svc, enable_v0_3_compat=True)
        assert app is not None
        assert mock_warn.call_count == 1
