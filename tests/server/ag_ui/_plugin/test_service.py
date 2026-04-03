# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for AgUiService."""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, Mock, NonCallableMock, patch

import pytest
from fastapi import FastAPI, HTTPException

from trpc_agent_sdk.server.ag_ui._core import AgUiAgent
from trpc_agent_sdk.server.ag_ui._plugin._service import AgUiService


def _mock_agent() -> NonCallableMock:
    """Create a non-callable mock so isinstance(..., Callable) is False."""
    return NonCallableMock(spec=AgUiAgent)


def _mock_app() -> Mock:
    app = Mock(spec=FastAPI)
    app.add_api_route = Mock()
    return app


class TestAgUiServiceInit:
    def test_defaults(self):
        svc = AgUiService("my_svc")
        assert svc.service_name == "my_svc"
        assert svc.app is None
        assert svc.agents == {}

    def test_with_app_and_agents(self):
        app = _mock_app()
        agent = _mock_agent()
        svc = AgUiService("svc", app=app, agents={"/chat": agent})

        assert svc.app is app
        assert svc.agents == {"/chat": agent}


class TestAgUiServiceProperties:
    def test_service_name(self):
        svc = AgUiService("test_name")
        assert svc.service_name == "test_name"

    def test_app_property(self):
        app = _mock_app()
        svc = AgUiService("svc", app=app)
        assert svc.app is app

    def test_agents_property(self):
        agent = _mock_agent()
        agents = {"/a": agent}
        svc = AgUiService("svc", agents=agents)
        assert svc.agents is agents


class TestCreateAgents:
    def test_no_factories_returns_none(self):
        svc = AgUiService("svc")
        result = svc.create_agents()
        assert result is None
        assert svc.agents == {}

    def test_creates_from_factories(self):
        agent_a = _mock_agent()
        agent_b = _mock_agent()
        factory_a = Mock(return_value=agent_a)
        factory_b = Mock(return_value=agent_b)

        app = _mock_app()
        svc = AgUiService("svc", app=app)
        svc.add_agent("/a", factory_a)
        svc.add_agent("/b", factory_b)

        svc.create_agents()

        factory_a.assert_called_once()
        factory_b.assert_called_once()
        assert svc.agents["/a"] is agent_a
        assert svc.agents["/b"] is agent_b


class TestAddAgent:
    def test_add_agent_instance(self):
        app = _mock_app()
        agent = _mock_agent()
        svc = AgUiService("svc", app=app)

        svc.add_agent("/chat", agent)

        assert svc.agents["/chat"] is agent
        app.add_api_route.assert_called_once_with(
            "/chat", svc._ag_ui_agent_endpoint, methods=["POST"], response_model=None
        )

    def test_add_agent_callable(self):
        app = _mock_app()
        agent = _mock_agent()
        factory = Mock(return_value=agent)
        svc = AgUiService("svc", app=app)

        svc.add_agent("/gen", factory)

        assert "/gen" not in svc.agents
        assert svc._agui_agent_factories["/gen"] is factory
        app.add_api_route.assert_called_once()


class TestSetFastapi:
    def test_sets_app(self):
        svc = AgUiService("svc")
        app = _mock_app()
        svc.set_fastapi(app)
        assert svc.app is app


class TestAgUiAgentEndpoint:
    async def test_agent_found_returns_streaming_response(self):
        app = _mock_app()
        agent = _mock_agent()
        svc = AgUiService("svc", app=app, agents={"/chat": agent})

        mock_input = Mock()
        mock_request = Mock()
        mock_request.headers = {"accept": "text/event-stream"}
        mock_request.url = Mock()
        mock_request.url.path = "/chat"

        with patch(
            "trpc_agent_sdk.server.ag_ui._plugin._service.EventEncoder"
        ) as MockEncoder, patch(
            "trpc_agent_sdk.server.ag_ui._plugin._service.event_generator"
        ) as mock_gen:
            encoder_instance = MockEncoder.return_value
            encoder_instance.get_content_type.return_value = "text/event-stream"
            mock_gen.return_value = iter([])

            response = await svc._ag_ui_agent_endpoint(mock_input, mock_request)

            MockEncoder.assert_called_once_with(accept="text/event-stream")
            mock_gen.assert_called_once_with(mock_request, agent, mock_input, encoder_instance)
            assert response.media_type == "text/event-stream"

    async def test_agent_not_found_raises_404(self):
        app = _mock_app()
        svc = AgUiService("svc", app=app, agents={})

        mock_input = Mock()
        mock_request = Mock()
        mock_request.headers = {"accept": "text/event-stream"}
        mock_request.url = Mock()
        mock_request.url.path = "/unknown"

        with patch(
            "trpc_agent_sdk.server.ag_ui._plugin._service.EventEncoder"
        ):
            with pytest.raises(HTTPException) as exc_info:
                await svc._ag_ui_agent_endpoint(mock_input, mock_request)

            assert exc_info.value.status_code == 404
            assert "Agent not found" in exc_info.value.detail
