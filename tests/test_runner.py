# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for the Runner class.

This test suite focuses on the core functionality of the Runner class,
including:
- Agent execution and event generation
- Session management and event appending
- Agent transfer functionality
- Human-in-the-loop scenarios
- Toolset cleanup
- Error handling
"""

import asyncio
from typing import AsyncGenerator
from unittest.mock import ANY
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.artifacts import BaseArtifactService
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import BaseMemoryService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


# Fixtures
@pytest.fixture
def mock_session_service():
    """Mock session service."""
    service = AsyncMock(spec=BaseSessionService)
    service.get_session = AsyncMock()
    service.create_session = AsyncMock()
    service.append_event = AsyncMock()
    service.create_session_summary = AsyncMock()
    service.close = AsyncMock()
    return service


@pytest.fixture
def mock_agent():
    """Mock base agent."""
    agent = Mock(spec=BaseAgent)
    agent.name = "test_agent"
    agent.sub_agents = []
    agent.get_subagents = Mock(return_value=[])
    agent.parent_agent = None
    agent.run_async = AsyncMock()
    agent.find_agent = Mock(return_value=None)
    return agent


@pytest.fixture
def mock_session():
    """Mock session object."""
    session = Session(
        id="test_session_id",
        app_name="test_app",
        user_id="test_user",
        save_key="test_save_key",
        state={},
        events=[],
        conversation_count=0,
        last_update_time=0.0,
    )
    return session


@pytest.fixture
def runner(mock_agent, mock_session_service):
    """Create a Runner instance with mocked dependencies."""
    return Runner(
        app_name="test_app",
        agent=mock_agent,
        session_service=mock_session_service,
    )


# Tests for Runner initialization
class TestRunnerInit:
    """Tests for Runner initialization."""

    def test_init_with_required_params(self, mock_agent, mock_session_service):
        """Test Runner initialization with required parameters."""
        runner = Runner(
            app_name="test_app",
            agent=mock_agent,
            session_service=mock_session_service,
        )

        assert runner.app_name == "test_app"
        assert runner.agent == mock_agent
        assert runner.session_service == mock_session_service
        assert runner.artifact_service is None
        assert runner.memory_service is None

    def test_init_with_all_params(self, mock_agent, mock_session_service):
        """Test Runner initialization with all parameters."""
        artifact_service = Mock(spec=BaseArtifactService)
        memory_service = Mock(spec=BaseMemoryService)

        runner = Runner(
            app_name="test_app",
            agent=mock_agent,
            session_service=mock_session_service,
            artifact_service=artifact_service,
            memory_service=memory_service,
        )

        assert runner.artifact_service == artifact_service
        assert runner.memory_service == memory_service


# Tests for run_async method
class TestRunAsync:
    """Tests for the main run_async method."""

    @pytest.mark.asyncio
    async def test_run_async_creates_new_session_when_not_found(self, runner, mock_session_service, mock_agent,
                                                                mock_session):
        """Test that run_async creates a new session when none exists."""
        # Setup
        mock_session_service.get_session.return_value = None
        mock_session_service.create_session.return_value = mock_session

        # Mock agent to return a simple event
        async def mock_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Execute
        events = []
        async for event in runner.run_async(
                user_id="test_user",
                session_id="test_session",
                new_message=Content(parts=[Part(text="Hello")]),
        ):
            events.append(event)

        # Verify
        mock_session_service.get_session.assert_called_once()
        mock_session_service.create_session.assert_called_once_with(
            app_name="test_app",
            user_id="test_user",
            session_id="test_session",
            agent_context=ANY,
        )
        assert len(events) == 1
        assert events[0].author == "test_agent"

    @pytest.mark.asyncio
    async def test_run_async_uses_existing_session(self, runner, mock_session_service, mock_agent, mock_session):
        """Test that run_async uses an existing session."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Execute
        events = []
        async for event in runner.run_async(
                user_id="test_user",
                session_id="test_session",
                new_message=Content(parts=[Part(text="Hello")]),
        ):
            events.append(event)

        # Verify
        mock_session_service.get_session.assert_called_once()
        mock_session_service.create_session.assert_not_called()
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_run_async_appends_user_message(self, runner, mock_session_service, mock_agent, mock_session):
        """Test that user messages are appended to the session."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run
        user_message = Content(parts=[Part(text="Hello")])

        # Execute
        async for _ in runner.run_async(
            user_id="test_user",
            session_id="test_session",
            new_message=user_message,
        ):
            pass

        # Verify that append_event was called for user message
        calls = mock_session_service.append_event.call_args_list
        user_event_call = [call for call in calls if call[1]['event'].author == 'user']
        assert len(user_event_call) >= 1
        assert user_event_call[0][1]['event'].content == user_message

    @pytest.mark.asyncio
    async def test_run_async_streaming_mode(self, runner, mock_session_service, mock_agent, mock_session):
        """Test streaming mode yields partial events."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            # Yield partial events
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Partial")]),
                partial=True,
            )
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Complete")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Execute with streaming enabled
        events = []
        async for event in runner.run_async(
                user_id="test_user",
                session_id="test_session",
                new_message=Content(parts=[Part(text="Hello")]),
                run_config=RunConfig(streaming=True),
        ):
            events.append(event)

        # Verify - should receive both partial and complete events
        assert len(events) == 2
        assert events[0].partial is True
        assert events[1].partial is False

    @pytest.mark.asyncio
    async def test_run_async_non_streaming_mode(self, runner, mock_session_service, mock_agent, mock_session):
        """Test non-streaming mode only yields complete events."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            # Yield partial events
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Partial")]),
                partial=True,
            )
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Complete")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Execute with streaming disabled
        events = []
        async for event in runner.run_async(
                user_id="test_user",
                session_id="test_session",
                new_message=Content(parts=[Part(text="Hello")]),
                run_config=RunConfig(streaming=False),
        ):
            events.append(event)

        # Verify - should only receive complete events
        assert len(events) == 1
        assert events[0].partial is False

    @pytest.mark.asyncio
    async def test_run_async_handles_empty_message(self, runner, mock_session_service, mock_agent, mock_session):
        """Test handling of messages with no parts."""
        # Setup
        mock_session_service.get_session.return_value = mock_session
        empty_message = Content(parts=[])

        # Execute and expect ValueError
        with pytest.raises(ValueError, match="No parts in the new_message"):
            async for _ in runner.run_async(
                    user_id="test_user",
                    session_id="test_session",
                    new_message=empty_message,
            ):
                pass

    @pytest.mark.asyncio
    async def test_run_async_increments_conversation_count(self, runner, mock_session_service, mock_agent,
                                                           mock_session):
        """Test that conversation count is incremented."""
        # Setup
        initial_count = mock_session.conversation_count
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Execute
        async for _ in runner.run_async(
            user_id="test_user",
            session_id="test_session",
            new_message=Content(parts=[Part(text="Hello")]),
        ):
            pass

        # Verify
        assert mock_session.conversation_count == initial_count + 1


# Tests for agent transfer functionality
class TestAgentTransfer:
    """Tests for agent transfer functionality."""

    @pytest.mark.asyncio
    async def test_agent_transfer_to_sub_agent(self, runner, mock_session_service, mock_agent, mock_session):
        """Test successful transfer from root agent to sub-agent."""
        # Setup sub-agent
        sub_agent = Mock(spec=BaseAgent)
        sub_agent.name = "sub_agent"
        sub_agent.parent_agent = mock_agent
        mock_agent.find_agent = Mock(return_value=sub_agent)

        mock_session_service.get_session.return_value = mock_session

        # Mock agent run with transfer request
        async def mock_agent_run(ctx):
            if ctx.agent.name == "test_agent":
                # Root agent requests transfer
                event = Event(
                    invocation_id=ctx.invocation_id,
                    author="test_agent",
                    content=Content(parts=[Part(text="Transferring")]),
                    partial=False,
                )
                event.actions = EventActions(transfer_to_agent="sub_agent")
                yield event

        async def mock_sub_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="sub_agent",
                content=Content(parts=[Part(text="Sub-agent response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run
        sub_agent.run_async = mock_sub_agent_run

        # Execute
        events = []
        async for event in runner.run_async(
                user_id="test_user",
                session_id="test_session",
                new_message=Content(parts=[Part(text="Hello")]),
        ):
            events.append(event)

        # Verify
        assert len(events) == 2
        assert events[0].author == "test_agent"
        assert events[1].author == "sub_agent"
        mock_agent.find_agent.assert_called_with("sub_agent")

    @pytest.mark.asyncio
    async def test_agent_transfer_target_not_found(self, runner, mock_session_service, mock_agent, mock_session):
        """Test handling when transfer target agent is not found."""
        # Setup
        mock_session_service.get_session.return_value = mock_session
        mock_agent.find_agent = Mock(return_value=None)

        async def mock_agent_run(ctx):
            event = Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Transferring")]),
                partial=False,
            )
            event.actions = EventActions(transfer_to_agent="nonexistent_agent")
            yield event

        mock_agent.run_async = mock_agent_run

        # Execute
        events = []
        async for event in runner.run_async(
                user_id="test_user",
                session_id="test_session",
                new_message=Content(parts=[Part(text="Hello")]),
        ):
            events.append(event)

        # Verify error event was generated
        error_events = [e for e in events if e.error_code == "transfer_target_not_found"]
        assert len(error_events) == 1
        assert "not found" in error_events[0].error_message


# Tests for _find_agent_to_run method
class TestFindAgentToRun:
    """Tests for the _find_agent_to_run method."""

    def test_find_agent_returns_root_when_no_events(self, runner, mock_agent, mock_session):
        """Test returns root agent when session has no events."""
        result = runner._find_agent_to_run(mock_session, mock_agent, RunConfig())
        assert result == mock_agent

    def test_find_agent_with_start_from_last_agent_disabled(self, runner, mock_agent, mock_session):
        """Test returns root agent when start_from_last_agent is disabled."""
        # Add some events
        mock_session.events.append(
            Event(
                invocation_id="inv_1",
                author="some_agent",
                content=Content(parts=[Part(text="Response")]),
            ))

        run_config = RunConfig(start_from_last_agent=False)
        result = runner._find_agent_to_run(mock_session, mock_agent, run_config)
        assert result == mock_agent

    def test_find_agent_with_human_in_the_loop(self, runner, mock_agent, mock_session):
        """Test finds agent when human-in-the-loop function response exists."""
        # Setup: agent made function call
        func_call = FunctionCall(id="func_123", name="test_function", args={"arg": "value"})
        agent_event = Event(
            invocation_id="inv_1",
            author="test_agent",
            content=Content(parts=[Part(function_call=func_call)]),
        )

        # User provides function response
        func_response = FunctionResponse(id="func_123", name="test_function", response={"result": "success"})
        user_event = Event(
            invocation_id="inv_2",
            author="user",
            content=Content(parts=[Part(function_response=func_response)]),
        )

        mock_session.events.extend([agent_event, user_event])

        # Execute
        result = runner._find_agent_to_run(mock_session, mock_agent, RunConfig())

        # Verify - should return the agent that made the function call
        assert result == mock_agent


# Tests for _is_transferable_across_agent_tree method
class TestIsTransferableAcrossAgentTree:
    """Tests for _is_transferable_across_agent_tree method."""

    def test_llm_agent_is_transferable(self, runner):
        """Test that LlmAgent is considered transferable."""
        from trpc_agent_sdk.agents._llm_agent import LlmAgent

        llm_agent = Mock(spec=LlmAgent)
        llm_agent.disallow_transfer_to_parent = False
        llm_agent.parent_agent = None

        result = runner._is_transferable_across_agent_tree(llm_agent)
        assert result is True

    def test_non_llm_agent_not_transferable(self, runner, mock_agent):
        """Test that non-LlmAgent is not transferable."""
        result = runner._is_transferable_across_agent_tree(mock_agent)
        assert result is False

    def test_agent_with_disallow_transfer_not_transferable(self, runner):
        """Test that agent with disallow_transfer_to_parent is not transferable."""
        from trpc_agent_sdk.agents._llm_agent import LlmAgent

        llm_agent = Mock(spec=LlmAgent)
        llm_agent.disallow_transfer_to_parent = True
        llm_agent.parent_agent = None

        result = runner._is_transferable_across_agent_tree(llm_agent)
        assert result is False


# Tests for _collect_toolset method
class TestCollectToolset:
    """Tests for _collect_toolset method."""

    def test_collect_toolset_from_agent_with_tools(self, runner, mock_agent):
        """Test collecting toolsets from agent with tools."""
        from trpc_agent_sdk.tools import BaseToolSet

        toolset1 = Mock(spec=BaseToolSet)
        toolset2 = Mock(spec=BaseToolSet)
        mock_agent.tools = [toolset1, toolset2]
        mock_agent.get_subagents.return_value = []

        result = runner._collect_toolset(mock_agent)

        assert len(result) == 2
        assert toolset1 in result
        assert toolset2 in result

    def test_collect_toolset_from_agent_hierarchy(self, runner, mock_agent):
        """Test collecting toolsets from agent hierarchy."""
        from trpc_agent_sdk.tools import BaseToolSet

        # Root agent tools
        toolset1 = Mock(spec=BaseToolSet)
        mock_agent.tools = [toolset1]

        # Sub-agent tools
        sub_agent = Mock(spec=BaseAgent)
        toolset2 = Mock(spec=BaseToolSet)
        sub_agent.tools = [toolset2]
        sub_agent.sub_agents = []
        sub_agent.get_subagents = Mock(return_value=[])

        mock_agent.sub_agents = [sub_agent]
        mock_agent.get_subagents.return_value = [sub_agent]

        result = runner._collect_toolset(mock_agent)

        assert len(result) == 2
        assert toolset1 in result
        assert toolset2 in result


# Tests for cleanup functionality
class TestCleanup:
    """Tests for cleanup and close methods."""

    @pytest.mark.asyncio
    async def test_cleanup_toolsets_success(self, runner):
        """Test successful cleanup of toolsets."""
        from trpc_agent_sdk.tools import BaseToolSet

        toolset1 = AsyncMock(spec=BaseToolSet)
        toolset2 = AsyncMock(spec=BaseToolSet)

        toolsets = {toolset1, toolset2}
        await runner._cleanup_toolsets(toolsets)

        toolset1.close.assert_called_once()
        toolset2.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_toolsets_handles_timeout(self, runner):
        """Test cleanup handles toolset timeout gracefully."""
        from trpc_agent_sdk.tools import BaseToolSet

        toolset = AsyncMock(spec=BaseToolSet)
        toolset.close = AsyncMock(side_effect=asyncio.TimeoutError())

        # Should not raise exception
        await runner._cleanup_toolsets({toolset})
        toolset.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_toolsets_handles_exception(self, runner):
        """Test cleanup handles toolset exception gracefully."""
        from trpc_agent_sdk.tools import BaseToolSet

        toolset = AsyncMock(spec=BaseToolSet)
        toolset.close = AsyncMock(side_effect=Exception("Close failed"))

        # Should not raise exception
        await runner._cleanup_toolsets({toolset})
        toolset.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_calls_all_services(self, runner, mock_session_service):
        """Test close method calls close on all services."""
        memory_service = AsyncMock(spec=BaseMemoryService)
        runner.memory_service = memory_service

        with patch.object(runner, '_collect_toolset', return_value=set()):
            await runner.close()

        mock_session_service.close.assert_called_once()
        memory_service.close.assert_called_once()


# Tests for session history handling
class TestSessionHistory:
    """Tests for handling session history."""

    @pytest.mark.asyncio
    async def test_run_async_with_history_content(self, runner, mock_session_service, mock_agent, mock_session):
        """Test handling of history content in messages."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Create message list with history
        history_message = Content(parts=[Part(text="Historical context")])
        user_message = Content(parts=[Part(text="Current message")])
        message_list = [history_message, user_message]

        # Execute with save_history enabled
        async for _ in runner.run_async(
            user_id="test_user",
            session_id="test_session",
            new_message=message_list,
            run_config=RunConfig(save_history_enabled=True),
        ):
            pass

        # Verify history event was appended
        calls = mock_session_service.append_event.call_args_list
        # Should have history event, user event, and agent response
        assert len(calls) >= 3

    @pytest.mark.asyncio
    async def test_run_async_triggers_summarization(self, runner, mock_session_service, mock_agent, mock_session):
        """Test that summarization is triggered after agent execution."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Execute
        async for _ in runner.run_async(
            user_id="test_user",
            session_id="test_session",
            new_message=Content(parts=[Part(text="Hello")]),
        ):
            pass

        # Verify summarization was called
        mock_session_service.create_session_summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_async_stores_in_memory_service(self, runner, mock_session_service, mock_agent, mock_session):
        """Test that session is stored in memory service when enabled."""
        # Setup
        memory_service = AsyncMock(spec=BaseMemoryService)
        memory_service.enabled = True
        runner.memory_service = memory_service

        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            yield Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Response")]),
                partial=False,
            )

        mock_agent.run_async = mock_agent_run

        # Execute
        async for _ in runner.run_async(
            user_id="test_user",
            session_id="test_session",
            new_message=Content(parts=[Part(text="Hello")]),
        ):
            pass

        # Verify memory service was called
        memory_service.store_session.assert_called_once()


# Tests for invisible events
class TestInvisibleEvents:
    """Tests for handling invisible events."""

    @pytest.mark.asyncio
    async def test_invisible_events_not_yielded(self, runner, mock_session_service, mock_agent, mock_session):
        """Test that invisible events are not yielded to caller."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            # Yield invisible event
            invisible_event = Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Invisible")]),
                partial=False,
                visible=False,
            )
            yield invisible_event

            # Yield visible event
            visible_event = Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Visible")]),
                partial=False,
                visible=True,
            )
            yield visible_event

        mock_agent.run_async = mock_agent_run

        # Execute
        events = []
        async for event in runner.run_async(
                user_id="test_user",
                session_id="test_session",
                new_message=Content(parts=[Part(text="Hello")]),
        ):
            events.append(event)

        # Verify - should only receive visible event
        assert len(events) == 1
        assert events[0].content.parts[0].text == "Visible"

    @pytest.mark.asyncio
    async def test_invisible_event_with_transfer_raises_error(self, runner, mock_session_service, mock_agent,
                                                              mock_session):
        """Test that invisible event with transfer request raises ValueError."""
        # Setup
        mock_session_service.get_session.return_value = mock_session

        async def mock_agent_run(ctx):
            event = Event(
                invocation_id=ctx.invocation_id,
                author="test_agent",
                content=Content(parts=[Part(text="Invisible transfer")]),
                partial=False,
                visible=False,
            )
            event.actions = EventActions(transfer_to_agent="other_agent")
            yield event

        mock_agent.run_async = mock_agent_run

        # Execute and expect ValueError
        with pytest.raises(ValueError, match="invisible is not allowed"):
            async for _ in runner.run_async(
                    user_id="test_user",
                    session_id="test_session",
                    new_message=Content(parts=[Part(text="Hello")]),
            ):
                pass
