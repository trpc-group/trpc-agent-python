# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for AgUiManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI

from trpc_agent_sdk.server.ag_ui._core import AgUiAgent
from trpc_agent_sdk.server.ag_ui._plugin._manager import AgUiManager
from trpc_agent_sdk.server.ag_ui._plugin._service import AgUiService


@pytest.fixture()
def mock_registry():
    registry = Mock()
    registry.register_service = Mock()
    registry.get_service = Mock(return_value={})
    return registry


@pytest.fixture()
def manager(mock_registry):
    with patch(
        "trpc_agent_sdk.server.ag_ui._plugin._manager.get_agui_service_registry",
        return_value=mock_registry,
    ):
        return AgUiManager()


class TestAgUiManagerInit:
    def test_init_defaults(self, manager, mock_registry):
        assert manager._agui_service_registry is mock_registry
        assert manager._agui_agents == {}
        assert manager._app is None

    def test_init_with_app(self, mock_registry):
        app = Mock(spec=FastAPI)
        with patch(
            "trpc_agent_sdk.server.ag_ui._plugin._manager.get_agui_service_registry",
            return_value=mock_registry,
        ):
            mgr = AgUiManager(app=app)
        assert mgr._app is app


class TestSetApp:
    def test_set_app(self, manager):
        app = Mock(spec=FastAPI)
        manager.set_app(app)
        assert manager._app is app


class TestRegisterService:
    def test_delegates_to_registry(self, manager, mock_registry):
        service = Mock(spec=AgUiService)
        manager.register_service("svc1", service)
        mock_registry.register_service.assert_called_once_with("svc1", service)


class TestGetService:
    def test_delegates_to_registry(self, manager, mock_registry):
        expected = Mock(spec=AgUiService)
        mock_registry.get_service.return_value = expected

        result = manager.get_service("svc1")

        mock_registry.get_service.assert_called_once_with("svc1")
        assert result is expected


class TestGetAgents:
    def test_returns_agents_dict(self, manager):
        assert manager.get_agents() == {}

    def test_returns_populated_agents(self, manager):
        agent = Mock(spec=AgUiAgent)
        manager._agui_agents["/chat"] = agent
        assert manager.get_agents() == {"/chat": agent}


class TestBuildAgents:
    def test_builds_agents_from_services(self, manager, mock_registry):
        agent_a = Mock(spec=AgUiAgent)
        agent_b = Mock(spec=AgUiAgent)

        svc1 = Mock(spec=AgUiService)
        svc1.agents = {"/a": agent_a}
        svc1.app = Mock(spec=FastAPI)

        svc2 = Mock(spec=AgUiService)
        svc2.agents = {"/b": agent_b}
        svc2.app = None

        mock_registry.get_service.return_value = {"svc1": svc1, "svc2": svc2}
        app = Mock(spec=FastAPI)
        manager._app = app

        manager._build_agents()

        svc1.create_agents.assert_called_once()
        svc2.create_agents.assert_called_once()
        assert manager._agui_agents == {"/a": agent_a, "/b": agent_b}
        svc1.set_fastapi.assert_not_called()
        svc2.set_fastapi.assert_called_once_with(app)

    def test_build_agents_no_services(self, manager, mock_registry):
        mock_registry.get_service.return_value = {}
        manager._build_agents()
        assert manager._agui_agents == {}


class TestRun:
    def test_run_calls_build_agents_and_uvicorn(self, manager, mock_registry):
        mock_registry.get_service.return_value = {}
        app = Mock(spec=FastAPI)
        manager._app = app

        with patch("trpc_agent_sdk.server.ag_ui._plugin._manager.uvicorn") as mock_uvicorn:
            manager.run("0.0.0.0", 8080, log_level="info")

            mock_uvicorn.run.assert_called_once_with(
                app, host="0.0.0.0", port=8080, log_level="info"
            )


class TestClose:
    async def test_close_calls_agent_close(self, manager):
        agent1 = AsyncMock(spec=AgUiAgent)
        agent2 = AsyncMock(spec=AgUiAgent)
        manager._agui_agents = {"/a": agent1, "/b": agent2}

        await manager.close()

        agent1.close.assert_awaited_once()
        agent2.close.assert_awaited_once()

    async def test_close_empty_agents(self, manager):
        await manager.close()
