# -*- coding: utf-8 -*-
"""Unit tests for trpc_agent_sdk.server.a2a._agent_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCapabilities, AgentCard

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.server.a2a._agent_service import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a.executor import TrpcA2aAgentExecutorConfig


def _make_agent(name="test-agent"):
    agent = MagicMock(spec=BaseAgent)
    agent.name = name
    agent.description = "Test agent"
    agent.sub_agents = []
    return agent


def _make_card(name="test-agent"):
    return AgentCard(
        name=name,
        description="Test agent",
        url="http://localhost",
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=True),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        skills=[],
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
class TestTrpcA2aAgentServiceInit:
    def test_basic(self):
        agent = _make_agent()
        service = TrpcA2aAgentService(service_name="svc", agent=agent)
        assert service._agent is agent
        assert service._service_name == "svc"
        assert service._agent_card is None
        assert service._session_service is None
        assert service._memory_service is None

    def test_with_card(self):
        agent = _make_agent()
        card = _make_card()
        service = TrpcA2aAgentService(service_name="svc", agent=agent, agent_card=card)
        assert service._agent_card is card

    def test_with_executor_config(self):
        agent = _make_agent()
        config = TrpcA2aAgentExecutorConfig(cancel_wait_timeout=5.0)
        service = TrpcA2aAgentService(service_name="svc", agent=agent, executor_config=config)
        assert service._executor_config is config


# ---------------------------------------------------------------------------
# agent_card property
# ---------------------------------------------------------------------------
class TestAgentCardProperty:
    def test_returns_none_before_init(self):
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent())
        assert service.agent_card is None

    def test_returns_card_when_set(self):
        card = _make_card()
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent(), agent_card=card)
        assert service.agent_card is card


# ---------------------------------------------------------------------------
# _initialize
# ---------------------------------------------------------------------------
class TestInitialize:
    async def test_creates_session_service_if_none(self):
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent())
        await service._initialize()
        assert service._session_service is not None

    async def test_builds_card_if_none(self):
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent())
        await service._initialize()
        assert service._agent_card is not None
        assert service._agent_card.capabilities.streaming is True

    async def test_preserves_existing_card(self):
        card = _make_card()
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent(), agent_card=card)
        await service._initialize()
        assert service._agent_card is card
        assert service._agent_card.capabilities.streaming is True


# ---------------------------------------------------------------------------
# get_agent_card
# ---------------------------------------------------------------------------
class TestGetAgentCard:
    async def test_returns_card(self):
        card = _make_card()
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent(), agent_card=card)
        result = await service.get_agent_card()
        assert result is card


# ---------------------------------------------------------------------------
# _create_executor
# ---------------------------------------------------------------------------
class TestCreateExecutor:
    def test_creates_executor(self):
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent())
        service._session_service = MagicMock()
        executor = service._create_executor()
        assert executor is not None


# ---------------------------------------------------------------------------
# execute / cancel
# ---------------------------------------------------------------------------
class TestExecuteAndCancel:
    async def test_execute_delegates_to_executor(self):
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent())
        service._session_service = MagicMock()

        mock_executor = AsyncMock()
        with patch.object(service, "_create_executor", return_value=mock_executor):
            ctx = MagicMock(spec=RequestContext)
            queue = MagicMock(spec=EventQueue)
            await service.execute(ctx, queue)
            mock_executor.execute.assert_awaited_once_with(ctx, queue)

    async def test_cancel_delegates_to_executor(self):
        service = TrpcA2aAgentService(service_name="svc", agent=_make_agent())
        service._session_service = MagicMock()

        mock_executor = AsyncMock()
        with patch.object(service, "_create_executor", return_value=mock_executor):
            ctx = MagicMock(spec=RequestContext)
            queue = MagicMock(spec=EventQueue)
            await service.cancel(ctx, queue)
            mock_executor.cancel.assert_awaited_once_with(ctx, queue)
