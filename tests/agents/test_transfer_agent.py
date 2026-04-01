# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for TransferAgent.

Reference: tests/teams/test_team_agent.py (fixtures, MockLLMModel, class layout),
           tests/test_runner.py (run_async event flow),
           tests/teams/test_delegation_signal.py (constant / init test layout).
"""

import asyncio
from typing import AsyncGenerator
from typing import List
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TransferAgent
from trpc_agent_sdk.agents._transfer_agent import TRPC_TRANSFER_AGENT_LIST_KEY
from trpc_agent_sdk.agents._transfer_agent import TRPC_TRANSFER_AGENT_RESULT_KEY
from trpc_agent_sdk.agents._transfer_agent import TRPC_TRANSFER_INSTRUCTION_KEY
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# Test model implementation that can be registered (same pattern as test_team_agent)
class MockLLMModel(LLMModel):
    """Mock LLM model for unit tests."""

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-.*"]

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: InvocationContext | None = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Test implementation."""
        yield LlmResponse(content=None)

    def validate_request(self, request: LlmRequest) -> None:
        """Test validation."""
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    """Register test model for all tests in this module."""
    # Save original registry
    original_registry = ModelRegistry._registry.copy()

    # Register test model
    ModelRegistry.register(MockLLMModel)

    yield

    # Restore original registry
    ModelRegistry._registry = original_registry


@pytest.fixture
def mock_target_agent():
    """Create a mock target agent."""
    agent = Mock(spec=BaseAgent)
    agent.name = "target_agent"
    agent.description = "Target agent"
    agent.sub_agents = []
    agent.parent_agent = None
    agent.find_agent = Mock(return_value=None)
    return agent


@pytest.fixture
def mock_sub_agent():
    """Create a mock sub-agent."""
    agent = Mock(spec=BaseAgent)
    agent.name = "sub_agent"
    agent.description = "Sub agent for routing"
    agent.sub_agents = []
    agent.parent_agent = None
    agent.find_agent = Mock(return_value=None)
    return agent


@pytest.fixture
def session():
    """Real session with mutable state for ctx.state."""
    return Session(
        id="session-1",
        app_name="test_app",
        user_id="user-1",
        save_key="test_app:user-1",
        state={},
        events=[],
    )


@pytest.fixture
def mock_session_service():
    """Create a mock session service (for tests that do not need real ctx.state)."""
    service = AsyncMock()
    service.get_session = AsyncMock()
    service.create_session = AsyncMock()
    service.append_event = AsyncMock()
    service.get_session_summary = AsyncMock(return_value="")
    return service


@pytest.fixture
def invocation_context(mock_target_agent):
    """Create a real InvocationContext; session_service is InMemorySessionService so Pydantic accepts it."""
    service = InMemorySessionService()
    created = asyncio.run(
        service.create_session(
            app_name="test_app",
            user_id="user-1",
            session_id="session-1",
        )
    )
    return InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=mock_target_agent,
        agent_context=create_agent_context(),
        session=created,
    )


class TestTransferAgentConstants:
    """Tests for TransferAgent state key constants."""

    def test_result_key_value(self):
        """TRPC_TRANSFER_AGENT_RESULT_KEY has expected value."""
        assert TRPC_TRANSFER_AGENT_RESULT_KEY == "trpc_transfer_agent_result"

    def test_instruction_key_value(self):
        """TRPC_TRANSFER_INSTRUCTION_KEY has expected value."""
        assert TRPC_TRANSFER_INSTRUCTION_KEY == "trpc_transfer_instruction"

    def test_agent_list_key_value(self):
        """TRPC_TRANSFER_AGENT_LIST_KEY has expected value."""
        assert TRPC_TRANSFER_AGENT_LIST_KEY == "trpc_transfer_agent_list"


class TestTransferAgentInit:
    """Tests for TransferAgent initialization."""

    def test_name_from_target(self, mock_target_agent):
        """Agent name is transfer_{target.name}_proxy."""
        model = MockLLMModel(model_name="test-model")
        agent = TransferAgent(
            agent=mock_target_agent,
            model=model,
        )
        assert agent.name == "target_agent_transfer_proxy"
        assert "transfer proxy" in agent.description.lower()

    def test_target_agent_property(self, mock_target_agent):
        """target_agent property returns the wrapped agent."""
        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(agent=mock_target_agent, model=model)
        assert transfer.target_agent is mock_target_agent

    def test_get_subagents_includes_target_and_sub_agents(
        self, mock_target_agent, mock_sub_agent
    ):
        """get_subagents returns target + sub_agents (no route agent)."""
        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(
            agent=mock_target_agent,
            model=model,
            sub_agents=[mock_sub_agent],
        )
        subagents = transfer.get_subagents()
        assert len(subagents) == 2
        assert subagents[0] is mock_target_agent
        assert subagents[1] is mock_sub_agent

    def test_get_subagents_no_sub_agents(self, mock_target_agent):
        """get_subagents with no sub_agents returns only target."""
        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(agent=mock_target_agent, model=model)
        assert transfer.get_subagents() == [mock_target_agent]

    def test_route_agent_created(self, mock_target_agent):
        """Internal _route_agent is LlmAgent with decision instruction."""
        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(agent=mock_target_agent, model=model)
        assert transfer._route_agent is not None
        assert isinstance(transfer._route_agent, LlmAgent)
        assert "target_agent_transfer" in transfer._route_agent.name

    def test_sub_agent_excluded_if_same_as_target(self, mock_target_agent):
        """Sub_agents list excludes the target agent."""
        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(
            agent=mock_target_agent,
            model=model,
            sub_agents=[mock_target_agent],
        )
        assert len(transfer._sub_agents) == 0
        assert transfer.get_subagents() == [mock_target_agent]


class TestTransferAgentFindSubAgent:
    """Tests for find_sub_agent."""

    def test_find_sub_agent_by_name(self, mock_target_agent, mock_sub_agent):
        """find_sub_agent resolves sub_agent by name."""
        mock_target_agent.find_agent = Mock(return_value=None)
        mock_sub_agent.find_agent = Mock(
            side_effect=lambda n: mock_sub_agent if n == "sub_agent" else None
        )
        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(
            agent=mock_target_agent,
            model=model,
            sub_agents=[mock_sub_agent],
        )
        found = transfer.find_sub_agent("sub_agent")
        assert found is mock_sub_agent

    def test_find_sub_agent_not_found(self, mock_target_agent, mock_sub_agent):
        """find_sub_agent returns None when name not found."""
        mock_target_agent.find_agent = Mock(return_value=None)
        mock_sub_agent.find_agent = Mock(return_value=None)
        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(
            agent=mock_target_agent,
            model=model,
            sub_agents=[mock_sub_agent],
        )
        assert transfer.find_sub_agent("nonexistent") is None


class TestTransferAgentRunAsync:
    """Tests for _run_async_impl."""

    def test_target_agent_called_first(
        self, mock_target_agent, mock_sub_agent, invocation_context
    ):
        """Target agent run_async is called first; then state is set; then route agent."""
        target_events = [
            Event(
                invocation_id=invocation_context.invocation_id,
                author="target_agent",
                content=Content(parts=[Part(text="target reply")]),
                partial=False,
            )
        ]

        async def target_run(ctx):
            for e in target_events:
                yield e

        mock_target_agent.run_async = target_run

        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(
            agent=mock_target_agent,
            model=model,
            sub_agents=[mock_sub_agent],
        )

        route_event = Event(
            invocation_id=invocation_context.invocation_id,
            author=transfer._route_agent.name,
            content=Content(parts=[Part.from_text(text="route reply")]),
            partial=False,
        )

        async def route_run(ctx):
            yield route_event

        # LlmAgent is Pydantic (no field run_async); set via object to bypass
        object.__setattr__(transfer._route_agent, "run_async", route_run)

        # Update ctx.agent for the run
        invocation_context.agent = transfer

        async def run():
            events = []
            async for event in transfer._run_async_impl(invocation_context):
                events.append(event)
            return events

        events = asyncio.run(run())

        assert len(events) == 2
        assert events[0].author == "target_agent"
        assert events[1].author == transfer._route_agent.name

        session = invocation_context.session
        assert session.state.get(TRPC_TRANSFER_AGENT_RESULT_KEY) == "target reply"
        assert TRPC_TRANSFER_INSTRUCTION_KEY in session.state
        assert session.state.get(TRPC_TRANSFER_AGENT_LIST_KEY) == "- sub_agent: Sub agent for routing"

    def test_state_result_empty_when_no_content(
        self, mock_target_agent, invocation_context
    ):
        """When target yields no text content, result key is '(no content)'."""
        async def target_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="target_agent",
                content=None,
                partial=False,
            )

        mock_target_agent.run_async = target_run

        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(agent=mock_target_agent, model=model)

        async def route_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author=transfer._route_agent.name,
                content=None,
                partial=False,
            )

        object.__setattr__(transfer._route_agent, "run_async", route_run)
        invocation_context.agent = transfer

        async def run():
            async for _ in transfer._run_async_impl(invocation_context):
                pass

        asyncio.run(run())
        assert invocation_context.session.state.get(TRPC_TRANSFER_AGENT_RESULT_KEY) == "(no content)"

    def test_state_agent_list_none_when_no_sub_agents(
        self, mock_target_agent, invocation_context
    ):
        """When there are no sub_agents, TRPC_TRANSFER_AGENT_LIST_KEY is 'None'."""
        async def target_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="target_agent",
                content=Content(parts=[Part(text="ok")]),
                partial=False,
            )

        mock_target_agent.run_async = target_run

        model = MockLLMModel(model_name="test-model")
        transfer = TransferAgent(agent=mock_target_agent, model=model)

        async def route_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author=transfer._route_agent.name,
                content=None,
                partial=False,
            )

        object.__setattr__(transfer._route_agent, "run_async", route_run)
        invocation_context.agent = transfer

        async def run():
            async for _ in transfer._run_async_impl(invocation_context):
                pass

        asyncio.run(run())
        assert invocation_context.session.state.get(TRPC_TRANSFER_AGENT_LIST_KEY) == "None"
