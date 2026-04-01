# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for TeamAgent."""

from typing import AsyncGenerator
from typing import List
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.teams.core import DELEGATION_SIGNAL_MARKER
from trpc_agent_sdk.teams.core import DelegationSignal
from trpc_agent_sdk.teams.core import DELEGATE_TOOL_NAME
from trpc_agent_sdk.teams.core import TEAM_STATE_KEY
from trpc_agent_sdk.teams.core import TeamRunContext
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


# Test model implementation that can be registered
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
def mock_session():
    """Create a mock session."""
    session = Mock()
    session.id = "session-123"
    session.app_name = "test_app"
    session.user_id = "user-123"
    session.state = {}
    session.events = []
    return session


@pytest.fixture
def mock_session_service():
    """Create a mock session service."""
    service = AsyncMock()
    service.get_session = AsyncMock()
    service.create_session = AsyncMock()
    service.append_event = AsyncMock()
    service.get_session_summary = AsyncMock(return_value="")
    return service


@pytest.fixture
def mock_invocation_context(mock_session, mock_session_service):
    """Create a mock invocation context."""
    ctx = Mock(spec=InvocationContext)
    ctx.invocation_id = "inv-123"
    ctx.session = mock_session
    ctx.session_service = mock_session_service
    ctx.branch = "team_agent"
    ctx.user_content = Content(role="user", parts=[Part.from_text(text="Hello")])

    # Make model_copy return a new mock with updated attributes
    def model_copy_side_effect(update=None):
        new_ctx = Mock(spec=InvocationContext)
        new_ctx.invocation_id = ctx.invocation_id
        new_ctx.session = ctx.session
        new_ctx.session_service = ctx.session_service
        new_ctx.branch = ctx.branch
        new_ctx.user_content = ctx.user_content
        if update:
            for key, value in update.items():
                setattr(new_ctx, key, value)
        new_ctx.model_copy = model_copy_side_effect
        return new_ctx

    ctx.model_copy = model_copy_side_effect
    return ctx


@pytest.fixture
def mock_member_agents():
    """Create mock member agents."""
    researcher = Mock(spec=LlmAgent)
    researcher.name = "researcher"
    researcher.description = "Researches information"
    researcher.model = MockLLMModel(model_name="test-model")
    researcher.tools = []

    writer = Mock(spec=LlmAgent)
    writer.name = "writer"
    writer.description = "Writes content"
    writer.model = MockLLMModel(model_name="test-model")
    writer.tools = []

    return [researcher, writer]


class TestTeamAgentInit:
    """Tests for TeamAgent initialization."""

    def test_basic_init(self, mock_member_agents):
        """Test basic TeamAgent initialization."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        assert team.name == "test_team"
        assert len(team.members) == 2

    def test_default_values(self, mock_member_agents):
        """Test TeamAgent default values."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        assert team.parallel_execution is False
        assert team.share_team_history is False
        assert team.share_member_interactions is False
        assert team.num_member_history_runs == 0
        assert team.add_history_to_leader is True
        assert team.max_iterations == 20

    def test_custom_configuration(self, mock_member_agents):
        """Test TeamAgent with custom configuration."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            parallel_execution=True,
            share_team_history=True,
            share_member_interactions=True,
            num_member_history_runs=2,
            max_iterations=10,
            num_history_runs=5,
        )

        assert team.parallel_execution is True
        assert team.share_team_history is True
        assert team.share_member_interactions is True
        assert team.num_member_history_runs == 2
        assert team.max_iterations == 10
        assert team.num_history_runs == 5

    def test_model_inherited_to_members(self):
        """Test that model is NOT inherited to members (they keep their own or empty)."""
        member = Mock(spec=LlmAgent)
        member.name = "member"
        member.model = ""  # No model
        member.tools = []

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=[member],
        )

        # Member keeps empty model - TeamAgent doesn't modify member models
        assert member.model == ""

    def test_model_not_overwritten_if_set(self):
        """Test that member's model is not overwritten if already set."""
        existing_model = MockLLMModel(model_name="test-model")
        member = Mock(spec=LlmAgent)
        member.name = "member"
        member.model = existing_model  # Already has model
        member.tools = []

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=[member],
        )

        # Member should keep its own model
        assert member.model == existing_model

    def test_leader_agent_initialized(self, mock_member_agents):
        """Test that internal leader agent is initialized."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        assert team._leader_agent is not None
        assert isinstance(team._leader_agent, LlmAgent)

    def test_leader_agent_has_delegation_tool(self, mock_member_agents):
        """Test that leader agent has delegation tool."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Check that leader has tools
        assert team._leader_agent.tools is not None
        assert len(team._leader_agent.tools) >= 1


class TestTeamAgentFindMember:
    """Tests for finding member agents."""

    def test_find_existing_member(self, mock_member_agents):
        """Test finding an existing member by name."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        member = team._find_member_by_name("researcher")
        assert member is not None
        assert member.name == "researcher"

    def test_find_nonexistent_member(self, mock_member_agents):
        """Test finding a non-existent member returns None."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        member = team._find_member_by_name("nonexistent")
        assert member is None


class TestExtractDelegationSignals:
    """Tests for extracting delegation signals from events."""

    def test_extract_delegation_signal(self, mock_member_agents):
        """Test extracting delegation signal from event."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Create event with delegation signal in function response
        signal = DelegationSignal(
            member_name="researcher",
            task="Find information",
        )
        function_response = FunctionResponse(
            name=DELEGATE_TOOL_NAME,
            response={"result": signal},
            id="func-123",
        )
        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part(function_response=function_response)],
            ),
        )

        signals = team._extract_delegation_signals(event)

        assert len(signals) == 1
        assert signals[0].member_name == "researcher"
        assert signals[0].task == "Find information"

    def test_extract_multiple_delegation_signals(self, mock_member_agents):
        """Test extracting multiple delegation signals from event."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Create event with multiple delegation signals
        signal1 = DelegationSignal(member_name="researcher", task="Task 1")
        signal2 = DelegationSignal(member_name="writer", task="Task 2")

        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[
                    Part(function_response=FunctionResponse(
                        name=DELEGATE_TOOL_NAME,
                        response={"result": signal1},
                        id="func-1",
                    )),
                    Part(function_response=FunctionResponse(
                        name=DELEGATE_TOOL_NAME,
                        response={"result": signal2},
                        id="func-2",
                    )),
                ],
            ),
        )

        signals = team._extract_delegation_signals(event)

        assert len(signals) == 2

    def test_no_delegation_signal(self, mock_member_agents):
        """Test event without delegation signal."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Create event without delegation signal
        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part.from_text(text="Just a response")],
            ),
        )

        signals = team._extract_delegation_signals(event)
        assert len(signals) == 0

    def test_extract_signal_from_dict_response(self, mock_member_agents):
        """Test extracting signal from dict response (serialized signal)."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Create event with dict response containing signal marker
        signal_dict = {
            "marker": DELEGATION_SIGNAL_MARKER,
            "action": "delegate_to_member",
            "member_name": "writer",
            "task": "Write article",
        }
        function_response = FunctionResponse(
            name=DELEGATE_TOOL_NAME,
            response={"result": signal_dict},
            id="func-123",
        )
        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part(function_response=function_response)],
            ),
        )

        signals = team._extract_delegation_signals(event)

        assert len(signals) == 1
        assert signals[0].member_name == "writer"

    def test_extract_signal_from_json_string_response(self, mock_member_agents):
        """Test extracting signal from JSON string response.

        This tests the case where FunctionTool serializes a Pydantic model
        via model_dump_json(), resulting in a JSON string wrapped as
        {"result": "<json_string>"}.
        """
        import json
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Create event with JSON string response (simulating model_dump_json() output)
        signal_json = json.dumps({
            "marker": DELEGATION_SIGNAL_MARKER,
            "action": "delegate_to_member",
            "member_name": "researcher",
            "task": "Research topic",
        })
        function_response = FunctionResponse(
            name=DELEGATE_TOOL_NAME,
            response={"result": signal_json},  # JSON string, not dict
            id="func-123",
        )
        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part(function_response=function_response)],
            ),
        )

        signals = team._extract_delegation_signals(event)

        assert len(signals) == 1
        assert signals[0].member_name == "researcher"
        assert signals[0].task == "Research topic"
        assert signals[0].marker == DELEGATION_SIGNAL_MARKER


class TestExtractTextFromEvent:
    """Tests for extracting text from events."""

    def test_extract_text_from_text_part(self, mock_member_agents):
        """Test extracting text from text parts."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part.from_text(text="Hello world")],
            ),
        )

        text = team._extract_text_from_event(event)
        assert text == "Hello world"

    def test_skips_thought_content(self, mock_member_agents):
        """Test that thought content is skipped."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[
                    Part(text="Visible", thought=False),
                    Part(text="Hidden", thought=True),
                ],
            ),
        )

        text = team._extract_text_from_event(event)
        assert "Visible" in text
        assert "Hidden" not in text

    def test_skips_delegation_tool_calls(self, mock_member_agents):
        """Test that delegation tool calls are skipped."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[
                    Part.from_text(text="Response"),
                    Part(function_call=FunctionCall(
                        name=DELEGATE_TOOL_NAME,
                        args={"member_name": "researcher"},
                        id="func-1",
                    )),
                ],
            ),
        )

        text = team._extract_text_from_event(event)
        assert "Response" in text
        assert DELEGATE_TOOL_NAME not in text


class TestExtractTextFromContent:
    """Tests for extracting text from content."""

    def test_extract_text_from_content(self, mock_member_agents):
        """Test basic text extraction from content."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        content = Content(
            role="user",
            parts=[Part.from_text(text="User message")],
        )

        text = team._extract_text_from_content(content)
        assert text == "User message"

    def test_extract_skips_thoughts(self, mock_member_agents):
        """Test that thoughts are skipped."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        content = Content(
            role="user",
            parts=[
                Part(text="Visible", thought=False),
                Part(text="Thought", thought=True),
            ],
        )

        text = team._extract_text_from_content(content)
        assert "Visible" in text
        assert "Thought" not in text

    def test_extract_empty_content(self, mock_member_agents):
        """Test extracting from empty content."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        assert team._extract_text_from_content(None) == ""
        assert team._extract_text_from_content(Content(role="user", parts=[])) == ""


class TestHasNonDelegationToolCalls:
    """Tests for detecting non-delegation tool calls."""

    def test_no_tool_calls(self, mock_member_agents):
        """Test event without tool calls."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part.from_text(text="Response")],
            ),
        )

        assert team._has_non_delegation_tool_calls(event) is False

    def test_only_delegation_tool_call(self, mock_member_agents):
        """Test event with only delegation tool call."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part(function_call=FunctionCall(
                    name=DELEGATE_TOOL_NAME,
                    args={},
                    id="func-1",
                ))],
            ),
        )

        assert team._has_non_delegation_tool_calls(event) is False

    def test_custom_tool_call(self, mock_member_agents):
        """Test event with custom (non-delegation) tool call."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        event = Event(
            invocation_id="inv-123",
            author="test_team",
            content=Content(
                role="model",
                parts=[Part(function_call=FunctionCall(
                    name="custom_tool",
                    args={},
                    id="func-1",
                ))],
            ),
        )

        assert team._has_non_delegation_tool_calls(event) is True


class TestCreateStateUpdateEvent:
    """Tests for creating state update events."""

    def test_create_state_update_event(self, mock_member_agents, mock_invocation_context):
        """Test creating state update event."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        ctx = TeamRunContext(team_name="test_team")
        ctx.add_leader_message("user", "Hello")

        event = team._create_state_update_event(mock_invocation_context, ctx)

        assert event.author == "test_team"
        assert event.partial is False
        assert TEAM_STATE_KEY in event.actions.state_delta

    def test_state_delta_contains_context(self, mock_member_agents, mock_invocation_context):
        """Test that state delta contains full context."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        ctx = TeamRunContext(team_name="test_team")
        ctx.add_leader_message("user", "Message")
        ctx.add_interaction("researcher", "Task", "Response")

        event = team._create_state_update_event(mock_invocation_context, ctx)

        state = event.actions.state_delta[TEAM_STATE_KEY]
        assert len(state["leader_history"]) == 1
        assert len(state["interactions"]) == 1


class TestHITLHelpers:
    """Tests for Human-in-the-loop helper methods."""

    def test_extract_function_response_from_content(self, mock_member_agents):
        """Test extracting function response from content."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        function_response = FunctionResponse(
            name="test_tool",
            response={"result": "success"},
            id="func-123",
        )
        content = Content(
            role="user",
            parts=[Part(function_response=function_response)],
        )

        result = team._extract_function_response_from_content(content)

        assert result is not None
        assert result.id == "func-123"
        assert result.name == "test_tool"

    def test_extract_function_response_none_content(self, mock_member_agents):
        """Test extraction with None content."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        assert team._extract_function_response_from_content(None) is None

    def test_extract_function_response_no_parts(self, mock_member_agents):
        """Test extraction with empty parts."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        content = Content(role="user", parts=[])
        assert team._extract_function_response_from_content(content) is None

    def test_extract_text_from_function_response_dict(self, mock_member_agents):
        """Test extracting text from dict function response."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        function_response = FunctionResponse(
            name="approval_tool",
            response={
                "approved": True,
                "reason": "Looks good"
            },
            id="func-123",
        )

        text = team._extract_text_from_function_response(function_response)

        assert "approval_tool" in text
        assert "approved" in text
        assert "reason" in text

    def test_extract_text_from_function_response_simple_dict(self, mock_member_agents):
        """Test extracting text from simple dict function response."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        function_response = FunctionResponse(
            name="input_tool",
            response={"input": "User input here"},
            id="func-123",
        )

        text = team._extract_text_from_function_response(function_response)

        assert "input_tool" in text
        assert "User input here" in text


class TestMemberMessageFilter:
    """Tests for member message filter functionality."""

    @pytest.mark.asyncio
    async def test_apply_default_filter(self, mock_member_agents):
        """Test applying default message filter."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        contents = [
            Content(role="model", parts=[Part.from_text(text="First")]),
            Content(role="model", parts=[Part.from_text(text="Second")]),
        ]

        result = await team._apply_member_message_filter("researcher", contents)

        # Default filter should keep all messages
        assert "First" in result
        assert "Second" in result

    @pytest.mark.asyncio
    async def test_apply_custom_single_filter(self, mock_member_agents):
        """Test applying single custom filter for all members."""
        from trpc_agent_sdk.teams.core import keep_last_member_message

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            member_message_filter=keep_last_member_message,
        )

        contents = [
            Content(role="model", parts=[Part.from_text(text="First")]),
            Content(role="model", parts=[Part.from_text(text="Last")]),
        ]

        result = await team._apply_member_message_filter("researcher", contents)

        # Last filter should only keep last message
        assert result == "Last"
        assert "First" not in result

    @pytest.mark.asyncio
    async def test_apply_per_member_filters(self, mock_member_agents):
        """Test applying per-member filters."""
        from trpc_agent_sdk.teams.core import keep_all_member_message, keep_last_member_message

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            member_message_filter={
                "researcher": keep_all_member_message,
                "writer": keep_last_member_message,
            },
        )

        contents = [
            Content(role="model", parts=[Part.from_text(text="First")]),
            Content(role="model", parts=[Part.from_text(text="Last")]),
        ]

        researcher_result = await team._apply_member_message_filter("researcher", contents)
        writer_result = await team._apply_member_message_filter("writer", contents)

        # Researcher should have all messages
        assert "First" in researcher_result
        assert "Last" in researcher_result

        # Writer should only have last
        assert writer_result == "Last"
        assert "First" not in writer_result

    @pytest.mark.asyncio
    async def test_per_member_filter_fallback_to_default(self, mock_member_agents):
        """Test per-member filter falls back to default for unconfigured member."""
        from trpc_agent_sdk.teams.core import keep_last_member_message

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            member_message_filter={
                "researcher": keep_last_member_message,
                # writer not configured - should use default
            },
        )

        contents = [
            Content(role="model", parts=[Part.from_text(text="First")]),
            Content(role="model", parts=[Part.from_text(text="Last")]),
        ]

        writer_result = await team._apply_member_message_filter("writer", contents)

        # Writer should use default (keep_all) since not in filter dict
        assert "First" in writer_result
        assert "Last" in writer_result


class TestTeamAgentWithInstruction:
    """Tests for TeamAgent instruction handling."""

    def test_instruction_passed_to_leader(self, mock_member_agents):
        """Test that team instruction is passed to leader agent."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            instruction="You are a helpful team coordinator",
        )

        # Leader agent instruction should contain team instruction
        assert "helpful team coordinator" in team._leader_agent.instruction

    def test_instruction_includes_members(self, mock_member_agents):
        """Test that leader instruction includes member information."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Leader instruction should mention members
        assert "researcher" in team._leader_agent.instruction
        assert "writer" in team._leader_agent.instruction


class TestTeamAgentWithTools:
    """Tests for TeamAgent custom tools handling."""

    def test_custom_tools_added_to_leader(self, mock_member_agents):
        """Test that custom tools are added to leader agent."""
        from trpc_agent_sdk.tools import FunctionTool

        def custom_calculator(a: int, b: int) -> int:
            """A custom calculator tool."""
            return a + b

        custom_tool = FunctionTool(func=custom_calculator)

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            tools=[custom_tool],
        )

        # Leader should have delegation tool + custom tool
        assert len(team._leader_agent.tools) >= 2

    def test_long_running_tool_tracking(self, mock_member_agents):
        """Test that long running tools are tracked."""
        from trpc_agent_sdk.tools import LongRunningFunctionTool

        def approval_function(data: str) -> str:
            """An approval tool."""
            return "approved"

        long_running_tool = LongRunningFunctionTool(func=approval_function)

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            tools=[long_running_tool],
        )

        # Long running tool should be tracked (name is derived from function name)
        assert "approval_function" in team._long_running_tool_names

    def test_skill_repository_passed_to_leader(self, mock_member_agents):
        """Test that TeamAgent skill_repository is propagated to internal leader agent."""
        skill_repository = Mock(spec=BaseSkillRepository)

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            skill_repository=skill_repository,
        )

        assert team.skill_repository is skill_repository
        assert team._leader_agent.skill_repository is skill_repository

    def test_skill_repository_default_none_on_leader(self, mock_member_agents):
        """Test that leader skill_repository defaults to None when not configured."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        assert team.skill_repository is None
        assert team._leader_agent.skill_repository is None


class TestParallelExecutionWithLock:
    """Tests for parallel execution with context_lock."""

    @pytest.mark.asyncio
    async def test_parallel_delegations_record_all_interactions(self, mock_member_agents, mock_invocation_context):
        """Test that parallel delegations correctly record all interactions."""
        import asyncio

        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
            parallel_execution=True,
        )

        # Mock both member agents
        async def mock_researcher_run(ctx):
            yield Event(
                invocation_id="inv-123",
                author="researcher",
                content=Content(role="model", parts=[Part.from_text(text="Research done")]),
                partial=False,
            )

        async def mock_writer_run(ctx):
            yield Event(
                invocation_id="inv-123",
                author="writer",
                content=Content(role="model", parts=[Part.from_text(text="Writing done")]),
                partial=False,
            )

        mock_member_agents[0].run_async = mock_researcher_run
        mock_member_agents[1].run_async = mock_writer_run

        # Create context with lock
        team_run_context = TeamRunContext(team_name="test_team")
        team_run_context.current_invocation_id = "inv-123"
        context_lock = asyncio.Lock()

        from trpc_agent_sdk.teams.core._message_builder import TeamMessageBuilder
        message_builder = TeamMessageBuilder()

        signals = [
            DelegationSignal(member_name="researcher", task="Research task"),
            DelegationSignal(member_name="writer", task="Writing task"),
        ]

        # Execute parallel delegations
        events = []
        async for event in team._execute_delegations_parallel(
                mock_invocation_context,
                signals,
                team_run_context,
                message_builder,
                is_member_mode=False,
                context_lock=context_lock,
        ):
            events.append(event)

        # Both interactions should be recorded
        assert len(team_run_context.interactions) == 2
        member_names = {i["member"] for i in team_run_context.interactions}
        assert "researcher" in member_names
        assert "writer" in member_names


class TestMemberModeHITLRestriction:
    """Tests for HITL restriction when TeamAgent runs as member."""

    @pytest.mark.asyncio
    async def test_member_hitl_raises_error_in_execute_delegation(self, mock_member_agents, mock_invocation_context):
        """Test that HITL from member raises RuntimeError in member mode."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Mock member to yield LongRunningEvent
        member = mock_member_agents[0]

        async def mock_run_with_hitl(ctx):
            yield LongRunningEvent(
                invocation_id="inv-123",
                author="researcher",
                function_call=FunctionCall(name="approval_tool", args={}, id="func-123"),
                function_response=FunctionResponse(name="approval_tool", response={}, id="func-123"),
            )

        member.run_async = mock_run_with_hitl

        team_run_context = TeamRunContext(team_name="test_team")
        team_run_context.current_invocation_id = "inv-123"

        from trpc_agent_sdk.teams.core._message_builder import TeamMessageBuilder
        message_builder = TeamMessageBuilder()

        signal = DelegationSignal(member_name="researcher", task="Task requiring approval")

        # Should raise RuntimeError when in member mode
        with pytest.raises(RuntimeError) as exc_info:
            async for _ in team._execute_delegation(
                    mock_invocation_context,
                    signal,
                    team_run_context,
                    message_builder,
                    is_member_mode=True,  # Running as member
                    context_lock=None,
            ):
                pass

        assert "member mode" in str(exc_info.value).lower()
        assert "Human-In-The-Loop" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_member_hitl_allowed_in_root_mode(self, mock_member_agents, mock_invocation_context):
        """Test that HITL from member is allowed when NOT in member mode."""
        team = TeamAgent(
            name="test_team",
            model=MockLLMModel(model_name="test-model"),
            members=mock_member_agents,
        )

        # Mock member to yield LongRunningEvent
        member = mock_member_agents[0]
        hitl_event = LongRunningEvent(
            invocation_id="inv-123",
            author="researcher",
            function_call=FunctionCall(name="approval_tool", args={}, id="func-123"),
            function_response=FunctionResponse(name="approval_tool", response={}, id="func-123"),
        )

        async def mock_run_with_hitl(ctx):
            yield hitl_event

        member.run_async = mock_run_with_hitl

        team_run_context = TeamRunContext(team_name="test_team")
        team_run_context.current_invocation_id = "inv-123"

        from trpc_agent_sdk.teams.core._message_builder import TeamMessageBuilder
        message_builder = TeamMessageBuilder()

        signal = DelegationSignal(member_name="researcher", task="Task requiring approval")

        # Should NOT raise error when NOT in member mode
        events = []
        async for event in team._execute_delegation(
                mock_invocation_context,
                signal,
                team_run_context,
                message_builder,
                is_member_mode=False,  # NOT running as member (root mode)
                context_lock=None,
        ):
            events.append(event)

        # HITL event should be yielded
        assert len(events) == 1
        assert isinstance(events[0], LongRunningEvent)
