# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a._application."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from a2a.server.request_handlers import DefaultRequestHandler
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


def _make_service(card: AgentCard | None):
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
        assert _jsonrpc_path_from_card(_make_card(url="https://agent.example.com/a2a/")) == "/a2a"

    def test_origin_slash_stays_root(self):
        assert _jsonrpc_path_from_card(_make_card(url="http://host:18081/")) == "/"

    def test_empty_url_defaults_to_root(self):
        assert _jsonrpc_path_from_card(_make_card(url="")) == "/"

    def test_origin_with_query_defaults_to_root(self):
        assert _jsonrpc_path_from_card(_make_card(url="http://host?q=1")) == "/"

    def test_origin_with_fragment_defaults_to_root(self):
        assert _jsonrpc_path_from_card(_make_card(url="http://host#frag")) == "/"

    def test_path_ignores_query_and_fragment(self):
        assert _jsonrpc_path_from_card(_make_card(url="https://agent.example.com/a2a?x=1#y")) == "/a2a"

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
    def test_none_agent_card_raises(self):
        # Card is auto-built in initialize(); assembling before that (or without
        # passing agent_card=) must fail with a clear contract error, not an
        # AttributeError inside the route factories.
        svc = _make_service(None)
        with pytest.raises(ValueError, match="agent_card is None"):
            create_a2a_application(svc)

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

    def test_default_does_not_serve_legacy_agent_json(self):
        from starlette.testclient import TestClient

        client = TestClient(create_a2a_application(_make_service(_make_card(url="http://host:18081"))))
        assert client.get("/.well-known/agent-card.json").status_code == 200
        assert client.get("/.well-known/agent.json").status_code == 404

    def test_compat_serves_legacy_agent_json(self):
        from starlette.testclient import TestClient

        client = TestClient(
            create_a2a_application(
                _make_service(_make_card(url="http://host:18081")),
                enable_v0_3_compat=True,
            ))
        modern = client.get("/.well-known/agent-card.json")
        legacy = client.get("/.well-known/agent.json")
        assert modern.status_code == 200
        assert legacy.status_code == 200
        assert modern.json() == legacy.json()

    def test_compat_does_not_rewrite_custom_handler_card(self):
        from starlette.testclient import TestClient

        card = _make_card(url="http://host:18081")
        svc = _make_service(card)
        handler = DefaultRequestHandler(
            agent_executor=svc,
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        app = create_a2a_application(
            svc, enable_v0_3_compat=True, request_handler=handler)
        handler_versions = [
            (i.protocol_binding, i.protocol_version)
            for i in handler._agent_card.supported_interfaces
        ]
        # Custom handlers are used as-is: compat patches the well-known card
        # copy only. Callers that need 0.3 on handler.agent_card must add it.
        assert ("JSONRPC", "0.3") not in handler_versions
        served = TestClient(app).get("/.well-known/agent-card.json").json()
        assert served.get("url") == "http://host:18081"

    def test_compat_adds_v03_interface_without_mutating_service_card(self):
        svc = _make_service(_make_card(url="http://host:18081"))
        with patch("trpc_agent_sdk.server.a2a._application.DefaultRequestHandler") as MockHandler:
            create_a2a_application(svc, enable_v0_3_compat=True)
        service_versions = [
            (i.protocol_binding, i.protocol_version)
            for i in svc.agent_card.supported_interfaces
        ]
        assert ("JSONRPC", "0.3") not in service_versions
        app_card = MockHandler.call_args.kwargs["agent_card"]
        assert app_card is not svc.agent_card
        app_versions = [
            (i.protocol_binding, i.protocol_version)
            for i in app_card.supported_interfaces
        ]
        assert ("JSONRPC", "1.0") in app_versions
        assert ("JSONRPC", "0.3") in app_versions

    def test_compat_with_missing_url_raises(self):
        # Compat exists so 0.3 clients can discover the service. An empty
        # interface url cannot be patched into a usable top-level url, so
        # starting would look successful while discovery still fails.
        svc = _make_service(_make_card(url=""))
        with pytest.raises(ValueError, match="enable_v0_3_compat requires a reachable"):
            create_a2a_application(svc, enable_v0_3_compat=True)

    def test_jsonrpc_mount_matches_card_url_path(self):
        # Deriving the path in _jsonrpc_path_from_card is not enough: the
        # Starlette JSON-RPC route must actually be mounted there, or clients
        # discover /a2a and POST to /.
        from urllib.parse import urlparse

        advertised = "https://x/a2a"
        card = _make_card(url=advertised)
        app = create_a2a_application(_make_service(card))

        expected_path = urlparse(advertised).path
        assert card.supported_interfaces[0].url == advertised
        assert expected_path == _jsonrpc_path_from_card(card)

        post_paths = [
            route.path
            for route in app.routes
            if "POST" in (getattr(route, "methods", None) or set())
        ]
        assert expected_path in post_paths
        assert "/" not in post_paths

        from starlette.testclient import TestClient

        client = TestClient(app)
        assert client.post(expected_path, json={}).status_code != 404
        assert client.post("/", json={}).status_code == 404

    def test_jsonrpc_mount_strips_trailing_slash(self):
        card = _make_card(url="https://x/a2a/")
        app = create_a2a_application(_make_service(card))
        assert _jsonrpc_path_from_card(card) == "/a2a"
        post_paths = [
            route.path
            for route in app.routes
            if "POST" in (getattr(route, "methods", None) or set())
        ]
        assert "/a2a" in post_paths
        assert "/a2a/" not in post_paths

        from starlette.testclient import TestClient

        client = TestClient(app, follow_redirects=False)
        assert client.post("/a2a", json={}).status_code != 404
