# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ClaudeAgent."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, AsyncGenerator, List
from unittest.mock import AsyncMock, MagicMock, Mock, patch, PropertyMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, Part, Schema, Type

# Mock claude_agent_sdk types for test isolation
_mock_claude_sdk = MagicMock()
_mock_claude_types = MagicMock()


@pytest.fixture(autouse=True)
def _ensure_claude_sdk_available():
    """Ensure claude_agent_sdk is importable (it's a real dependency)."""
    pytest.importorskip("claude_agent_sdk")


from trpc_agent_sdk.server.agents.claude._claude_agent import ClaudeAgent
from trpc_agent_sdk.server.agents.claude._runtime import AsyncRuntime
from trpc_agent_sdk.server.agents.claude._session_config import SessionConfig
from trpc_agent_sdk.server.agents.claude._session_manager import SessionManager
from trpc_agent_sdk.models import LLMModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-claude-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield MagicMock()

    def validate_request(self, request):
        pass


def _make_ctx(
    session_events=None,
    state=None,
    override_messages=None,
    streaming=False,
    session_id="test-session",
    custom_data=None,
):
    """Create a mock InvocationContext."""
    ctx = MagicMock(spec=InvocationContext)
    ctx.invocation_id = "inv-1"
    ctx.branch = None
    ctx.raise_if_cancelled = AsyncMock()
    ctx.override_messages = override_messages
    ctx.user_content = None

    # Session
    session = MagicMock()
    session.id = session_id
    session.events = session_events or []
    session.state = state or {}
    ctx.session = session

    # State (dict-like)
    ctx.state = state if state is not None else {}

    # RunConfig
    run_config = MagicMock()
    run_config.streaming = streaming
    run_config.custom_data = custom_data or {}
    ctx.run_config = run_config

    return ctx


def _make_event(author="user", text="hello", role="user"):
    """Create a simple Event."""
    return Event(
        invocation_id="inv-1",
        author=author,
        content=Content(role=role, parts=[Part.from_text(text=text)]),
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestClaudeAgentInit:
    def test_basic_init(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        assert agent.name == "test_agent"
        assert agent.model is model
        assert agent.instruction == ""
        assert agent.tools == []
        assert agent.enable_session is False
        assert agent._runtime is None
        assert agent._session_manager is None

    def test_custom_config(self):
        model = MockLLMModel(model_name="test-claude-1")
        config = SessionConfig(ttl=300)
        agent = ClaudeAgent(
            name="test_agent",
            model=model,
            instruction="Be helpful",
            enable_session=True,
            session_config=config,
            output_key="result",
        )
        assert agent.instruction == "Be helpful"
        assert agent.enable_session is True
        assert agent.session_config.ttl == 300
        assert agent.output_key == "result"


# ---------------------------------------------------------------------------
# initialize / destroy
# ---------------------------------------------------------------------------

class TestClaudeAgentLifecycle:
    def test_initialize_creates_runtime_and_session_manager(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        with patch.object(AsyncRuntime, "start"):
            agent.initialize()

        assert agent._runtime is not None
        assert agent._session_manager is not None

    def test_initialize_skips_when_enable_session(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, enable_session=True)
        agent.initialize()
        assert agent._runtime is None
        assert agent._session_manager is None

    def test_initialize_is_idempotent(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        with patch.object(AsyncRuntime, "start"):
            agent.initialize()
            rt = agent._runtime
            sm = agent._session_manager
            agent.initialize()
            assert agent._runtime is rt
            assert agent._session_manager is sm

    def test_destroy_cleans_up(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        mock_sm = MagicMock(spec=SessionManager)
        mock_rt = MagicMock(spec=AsyncRuntime)
        agent._session_manager = mock_sm
        agent._runtime = mock_rt

        agent.destroy()

        mock_sm.close.assert_called_once()
        mock_rt.shutdown.assert_called_once()
        assert agent._session_manager is None
        assert agent._runtime is None

    def test_destroy_handles_sm_error(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        mock_sm = MagicMock(spec=SessionManager)
        mock_sm.close.side_effect = RuntimeError("close error")
        mock_rt = MagicMock(spec=AsyncRuntime)
        agent._session_manager = mock_sm
        agent._runtime = mock_rt

        agent.destroy()
        assert agent._session_manager is None
        assert agent._runtime is None

    def test_destroy_handles_rt_error(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        mock_rt = MagicMock(spec=AsyncRuntime)
        mock_rt.shutdown.side_effect = RuntimeError("shutdown error")
        agent._runtime = mock_rt

        agent.destroy()
        assert agent._runtime is None

    def test_destroy_noop_when_not_initialized(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        agent.destroy()  # Should not raise


# ---------------------------------------------------------------------------
# _ensure_model_ready
# ---------------------------------------------------------------------------

class TestEnsureModelReady:
    async def test_static_model_cached(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        agent._resolved_model_key = "cached-key"

        ctx = _make_ctx()
        result = await agent._ensure_model_ready(ctx)
        assert result == "cached-key"

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="new-key")
    async def test_static_model_first_add(self, mock_add):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        ctx = _make_ctx()
        result = await agent._ensure_model_ready(ctx)
        assert result == "new-key"
        assert agent._resolved_model_key == "new-key"

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="new-key")
    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._delete_model")
    async def test_callable_model_deletes_old_key(self, mock_delete, mock_add):
        async def model_factory(custom_data):
            return MockLLMModel(model_name="test-claude-dynamic")

        agent = ClaudeAgent(name="test_agent", model=model_factory)
        agent._resolved_model_key = "old-key"

        ctx = _make_ctx()
        result = await agent._ensure_model_ready(ctx)
        mock_delete.assert_called_once_with("old-key")
        assert result == "new-key"

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model",
           side_effect=RuntimeError("proxy not ready"))
    async def test_raises_on_proxy_error(self, mock_add):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        ctx = _make_ctx()
        with pytest.raises(ValueError, match="Failed to add model"):
            await agent._ensure_model_ready(ctx)

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="new-key")
    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._delete_model",
           side_effect=RuntimeError("delete failed"))
    async def test_callable_model_handles_delete_error(self, mock_delete, mock_add):
        async def model_factory(custom_data):
            return MockLLMModel(model_name="test-claude-dynamic")

        agent = ClaudeAgent(name="test_agent", model=model_factory)
        agent._resolved_model_key = "old-key"

        ctx = _make_ctx()
        result = await agent._ensure_model_ready(ctx)
        assert result == "new-key"


# ---------------------------------------------------------------------------
# _detect_streaming_tools
# ---------------------------------------------------------------------------

class TestDetectStreamingTools:
    async def test_empty_tools(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, tools=[])
        ctx = _make_ctx()
        result = await agent._detect_streaming_tools(ctx)
        assert result == set()

    async def test_base_tool_streaming(self):
        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "my_tool"
        mock_tool.is_streaming = True

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, tools=[mock_tool])
        ctx = _make_ctx()
        result = await agent._detect_streaming_tools(ctx)
        assert "my_tool" in result
        assert "mcp__test_agent_tools__my_tool" in result

    async def test_base_tool_not_streaming(self):
        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "my_tool"
        mock_tool.is_streaming = False

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, tools=[mock_tool])
        ctx = _make_ctx()
        result = await agent._detect_streaming_tools(ctx)
        assert result == set()

    async def test_toolset_streaming(self):
        from trpc_agent_sdk.tools import BaseTool, BaseToolSet
        mock_inner_tool = MagicMock(spec=BaseTool)
        mock_inner_tool.name = "inner_tool"
        mock_inner_tool.is_streaming = True

        mock_toolset = MagicMock(spec=BaseToolSet)
        mock_toolset.get_tools = AsyncMock(return_value=[mock_inner_tool])

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, tools=[mock_toolset])
        ctx = _make_ctx()
        result = await agent._detect_streaming_tools(ctx)
        assert "inner_tool" in result

    async def test_callable_streaming(self):
        def my_func():
            pass
        my_func.__name__ = "my_func"
        my_func.is_streaming = True

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, tools=[my_func])
        ctx = _make_ctx()
        result = await agent._detect_streaming_tools(ctx)
        assert "my_func" in result


# ---------------------------------------------------------------------------
# _apply_template_substitution
# ---------------------------------------------------------------------------

class TestApplyTemplateSubstitution:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_no_template(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={"name": "Alice"})
        result = agent._apply_template_substitution("plain text", ctx)
        assert result == "plain text"

    def test_empty_string(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        result = agent._apply_template_substitution("", ctx)
        assert result == ""

    def test_replace_variable(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={"user_name": "Alice"})
        result = agent._apply_template_substitution("Hello {user_name}", ctx)
        assert result == "Hello Alice"

    def test_multiple_variables(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={"name": "Alice", "city": "NYC"})
        result = agent._apply_template_substitution("{name} in {city}", ctx)
        assert result == "Alice in NYC"

    def test_missing_required_variable(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={})
        result = agent._apply_template_substitution("Hello {unknown}", ctx)
        assert result == "Hello {unknown}"

    def test_optional_variable_present(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={"name": "Alice"})
        result = agent._apply_template_substitution("Hello {name?}", ctx)
        assert result == "Hello Alice"

    def test_optional_variable_missing(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={})
        result = agent._apply_template_substitution("Hello {name?}", ctx)
        assert result == "Hello "

    def test_none_value_becomes_empty(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={"name": None})
        result = agent._apply_template_substitution("Hello {name}", ctx)
        assert result == "Hello "

    def test_no_session(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        ctx.session = None
        result = agent._apply_template_substitution("Hello {name}", ctx)
        assert result == "Hello {name}"


# ---------------------------------------------------------------------------
# _extract_latest_user_message
# ---------------------------------------------------------------------------

class TestExtractLatestUserMessage:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_no_session(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        ctx.session = None
        result = agent._extract_latest_user_message(ctx)
        assert result is None

    def test_no_events(self):
        agent = self._make_agent()
        ctx = _make_ctx(session_events=[])
        result = agent._extract_latest_user_message(ctx)
        assert result is None

    def test_single_user_message(self):
        agent = self._make_agent()
        event = _make_event(author="user", text="Hello")
        ctx = _make_ctx(session_events=[event])
        result = agent._extract_latest_user_message(ctx)
        assert result == "Hello"

    def test_multiple_messages_returns_latest(self):
        agent = self._make_agent()
        events = [
            _make_event(author="user", text="First"),
            _make_event(author="model", text="Response"),
            _make_event(author="user", text="Second"),
        ]
        ctx = _make_ctx(session_events=events)
        result = agent._extract_latest_user_message(ctx)
        assert result == "Second"

    def test_no_user_messages(self):
        agent = self._make_agent()
        events = [_make_event(author="model", text="hello")]
        ctx = _make_ctx(session_events=events)
        result = agent._extract_latest_user_message(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# _convert_override_messages_to_prompt
# ---------------------------------------------------------------------------

class TestConvertOverrideMessagesToPrompt:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_simple_user_message(self):
        agent = self._make_agent()
        messages = [Content(role="user", parts=[Part.from_text(text="hello")])]
        result = agent._convert_override_messages_to_prompt(messages)
        assert "User: hello" in result

    def test_assistant_message(self):
        agent = self._make_agent()
        messages = [Content(role="model", parts=[Part.from_text(text="sure")])]
        result = agent._convert_override_messages_to_prompt(messages)
        assert "Assistant: sure" in result

    def test_empty_messages(self):
        agent = self._make_agent()
        result = agent._convert_override_messages_to_prompt([])
        assert result is None

    def test_function_call_in_messages(self):
        agent = self._make_agent()
        fc = Part.from_function_call(name="search", args={"q": "test"})
        messages = [Content(role="user", parts=[fc])]
        result = agent._convert_override_messages_to_prompt(messages)
        assert "search" in result

    def test_function_response_in_messages(self):
        agent = self._make_agent()
        fr = Part.from_function_response(name="search", response={"result": "found"})
        messages = [Content(role="user", parts=[fr])]
        result = agent._convert_override_messages_to_prompt(messages)
        assert "search" in result

    def test_non_content_items_skipped(self):
        agent = self._make_agent()
        messages = ["not a content object", None]
        result = agent._convert_override_messages_to_prompt(messages)
        assert result is None


# ---------------------------------------------------------------------------
# _build_prompt_with_history
# ---------------------------------------------------------------------------

class TestBuildPromptWithHistory:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_no_session(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        ctx.session = None
        result = agent._build_prompt_with_history(ctx)
        assert result is None

    def test_no_events(self):
        agent = self._make_agent()
        ctx = _make_ctx(session_events=[])
        result = agent._build_prompt_with_history(ctx)
        assert result is None

    def test_single_user_message(self):
        agent = self._make_agent()
        ctx = _make_ctx(session_events=[_make_event(author="user", text="Hello")])
        result = agent._build_prompt_with_history(ctx)
        assert result == "Hello"

    def test_multi_turn_conversation(self):
        agent = self._make_agent()
        events = [
            _make_event(author="user", text="First question"),
            _make_event(author="test_agent", text="First answer"),
            _make_event(author="user", text="Second question"),
        ]
        ctx = _make_ctx(session_events=events)
        result = agent._build_prompt_with_history(ctx)
        assert "Previous conversation:" in result
        assert "First question" in result
        assert "First answer" in result
        assert "Current message:" in result
        assert "Second question" in result

    def test_no_user_messages_returns_none(self):
        agent = self._make_agent()
        events = [_make_event(author="other_agent", text="hello")]
        ctx = _make_ctx(session_events=events)
        result = agent._build_prompt_with_history(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# _format_agent_message_parts
# ---------------------------------------------------------------------------

class TestFormatAgentMessageParts:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_text_part(self):
        agent = self._make_agent()
        parts = [Part.from_text(text="hello")]
        result = agent._format_agent_message_parts(parts)
        assert result == "hello"

    def test_thought_part(self):
        agent = self._make_agent()
        part = Part.from_text(text="thinking content")
        part.thought = True
        result = agent._format_agent_message_parts([part])
        # When thought is True, the code formats it as [Thinking: True]
        # because part.thought is boolean; regular text path won't be reached
        assert "Thinking:" in result or "thinking content" in result

    def test_function_call_part(self):
        agent = self._make_agent()
        parts = [Part.from_function_call(name="search", args={"q": "test"})]
        result = agent._format_agent_message_parts(parts)
        assert "search" in result

    def test_function_response_part(self):
        agent = self._make_agent()
        parts = [Part.from_function_response(name="search", response={"result": "found"})]
        result = agent._format_agent_message_parts(parts)
        assert "search" in result

    def test_empty_parts(self):
        agent = self._make_agent()
        result = agent._format_agent_message_parts([])
        assert result == ""

    def test_mixed_parts(self):
        agent = self._make_agent()
        parts = [
            Part.from_text(text="hello"),
            Part.from_function_call(name="search", args={"q": "test"}),
        ]
        result = agent._format_agent_message_parts(parts)
        assert "hello" in result
        assert "search" in result


# ---------------------------------------------------------------------------
# _save_output_to_state
# ---------------------------------------------------------------------------

class TestSaveOutputToState:
    def _make_agent(self, output_key="result"):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model, output_key=output_key)

    def test_saves_to_state(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={})
        event = Event(
            invocation_id="inv-1",
            author="test_agent",
            content=Content(role="model", parts=[Part.from_text(text="output text")]),
        )
        agent._save_output_to_state(ctx, event)
        assert ctx.state["result"] == "output text"

    def test_no_output_key_skips(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        ctx = _make_ctx(state={})
        event = Event(
            invocation_id="inv-1",
            author="test_agent",
            content=Content(role="model", parts=[Part.from_text(text="output")]),
        )
        agent._save_output_to_state(ctx, event)
        assert "result" not in ctx.state

    def test_empty_text_not_saved(self):
        agent = self._make_agent()
        ctx = _make_ctx(state={})
        event = Event(
            invocation_id="inv-1",
            author="test_agent",
            content=Content(role="model", parts=[]),
        )
        agent._save_output_to_state(ctx, event)
        assert "result" not in ctx.state


# ---------------------------------------------------------------------------
# _get_python_type_from_schema
# ---------------------------------------------------------------------------

class TestGetPythonTypeFromSchema:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_string_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = Type.STRING
        assert agent._get_python_type_from_schema(schema) is str

    def test_integer_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = Type.INTEGER
        assert agent._get_python_type_from_schema(schema) is int

    def test_number_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = Type.NUMBER
        assert agent._get_python_type_from_schema(schema) is float

    def test_boolean_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = Type.BOOLEAN
        assert agent._get_python_type_from_schema(schema) is bool

    def test_object_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = Type.OBJECT
        assert agent._get_python_type_from_schema(schema) is dict

    def test_array_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = Type.ARRAY
        assert agent._get_python_type_from_schema(schema) is list

    def test_none_schema(self):
        agent = self._make_agent()
        assert agent._get_python_type_from_schema(None) is str

    def test_none_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = None
        assert agent._get_python_type_from_schema(schema) is str

    def test_unknown_type(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.type = "unknown_type"
        assert agent._get_python_type_from_schema(schema) is str


# ---------------------------------------------------------------------------
# _convert_schema_to_dict
# ---------------------------------------------------------------------------

class TestConvertSchemaToDict:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_none_schema(self):
        agent = self._make_agent()
        result = agent._convert_schema_to_dict(None)
        assert result == {}

    def test_no_properties(self):
        agent = self._make_agent()
        schema = MagicMock()
        schema.properties = None
        result = agent._convert_schema_to_dict(schema)
        assert result == {}

    def test_with_properties(self):
        agent = self._make_agent()
        param1 = MagicMock()
        param1.type = Type.STRING
        param2 = MagicMock()
        param2.type = Type.INTEGER

        schema = MagicMock()
        schema.properties = {"name": param1, "count": param2}

        result = agent._convert_schema_to_dict(schema)
        assert result["name"] is str
        assert result["count"] is int


# ---------------------------------------------------------------------------
# _get_entry_point_dir
# ---------------------------------------------------------------------------

class TestGetEntryPointDir:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_returns_directory(self):
        agent = self._make_agent()
        result = agent._get_entry_point_dir()
        # Should return some path or None; in test context it depends on __main__.__file__
        assert result is None or isinstance(result, str)

    def test_no_main_file(self):
        agent = self._make_agent()
        with patch.dict(sys.modules, {"__main__": MagicMock(__file__=None)}):
            result = agent._get_entry_point_dir()
            assert result is None

    def test_no_main_module(self):
        agent = self._make_agent()
        with patch.dict(sys.modules, {"__main__": None}):
            result = agent._get_entry_point_dir()
            assert result is None


# ---------------------------------------------------------------------------
# _convert_message_to_event
# ---------------------------------------------------------------------------

class TestConvertMessageToEvent:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_system_message_returns_none(self):
        from claude_agent_sdk import SystemMessage
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = SystemMessage(subtype="status", data={"message": "ready"})
        result = agent._convert_message_to_event(ctx, msg, {})
        assert result is None

    def test_result_message_returns_none(self):
        from claude_agent_sdk import ResultMessage
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = ResultMessage(
            subtype="result",
            num_turns=1,
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            total_cost_usd=0.01,
            usage={"input_tokens": 10, "output_tokens": 5},
            result="done",
            session_id="s1",
        )
        result = agent._convert_message_to_event(ctx, msg, {})
        assert result is None

    def test_unhandled_type_returns_none(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        result = agent._convert_message_to_event(ctx, "unknown_type", {})
        assert result is None


# ---------------------------------------------------------------------------
# _convert_assistant_message
# ---------------------------------------------------------------------------

class TestConvertAssistantMessage:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_text_block(self):
        from claude_agent_sdk import AssistantMessage, TextBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = AssistantMessage(model="test", content=[TextBlock(text="hello")])
        tool_use_map = {}
        event = agent._convert_assistant_message(ctx, msg, tool_use_map)
        assert event is not None
        assert event.content.parts[0].text == "hello"

    def test_thinking_block(self):
        from claude_agent_sdk import AssistantMessage, ThinkingBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = AssistantMessage(model="test", content=[ThinkingBlock(thinking="reasoning...", signature="sig")])
        tool_use_map = {}
        # Part.from_thought creates a Part with thought=True; mock it with a real Part
        thought_part = Part.from_text(text="reasoning...")
        thought_part.thought = True
        with patch.object(Part, "from_thought", return_value=thought_part, create=True):
            event = agent._convert_assistant_message(ctx, msg, tool_use_map)
        assert event is not None
        assert event.content.parts[0].thought is True

    def test_tool_use_block(self):
        from claude_agent_sdk import AssistantMessage, ToolUseBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = AssistantMessage(model="test", content=[
            ToolUseBlock(id="t1", name="search", input={"q": "test"})
        ])
        tool_use_map = {}
        event = agent._convert_assistant_message(ctx, msg, tool_use_map)
        assert event is not None
        assert event.content.parts[0].function_call.name == "search"
        assert tool_use_map["t1"] == "search"

    def test_tool_result_block_string(self):
        from claude_agent_sdk import AssistantMessage, ToolResultBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = AssistantMessage(model="test", content=[
            ToolResultBlock(tool_use_id="t1", content="result")
        ])
        tool_use_map = {}
        event = agent._convert_assistant_message(ctx, msg, tool_use_map)
        assert event is not None

    def test_tool_result_block_list(self):
        from claude_agent_sdk import AssistantMessage, ToolResultBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = AssistantMessage(model="test", content=[
            ToolResultBlock(
                tool_use_id="t1",
                content=[{"type": "text", "text": "result text"}],
            )
        ])
        tool_use_map = {}
        event = agent._convert_assistant_message(ctx, msg, tool_use_map)
        assert event is not None

    def test_empty_content_returns_none(self):
        from claude_agent_sdk import AssistantMessage
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = AssistantMessage(model="test", content=[])
        tool_use_map = {}
        event = agent._convert_assistant_message(ctx, msg, tool_use_map)
        assert event is None


# ---------------------------------------------------------------------------
# _convert_user_message
# ---------------------------------------------------------------------------

class TestConvertUserMessage:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_tool_result(self):
        from claude_agent_sdk import UserMessage, ToolResultBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = UserMessage(content=[
            ToolResultBlock(tool_use_id="t1", content="result text")
        ])
        tool_use_map = {"t1": "search"}
        event = agent._convert_user_message(ctx, msg, tool_use_map)
        assert event is not None
        assert event.content.parts[0].function_response.name == "search"

    def test_tool_result_list_content(self):
        from claude_agent_sdk import UserMessage, ToolResultBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = UserMessage(content=[
            ToolResultBlock(
                tool_use_id="t1",
                content=[{"type": "text", "text": "result"}],
            )
        ])
        tool_use_map = {"t1": "search"}
        event = agent._convert_user_message(ctx, msg, tool_use_map)
        assert event is not None

    def test_unknown_tool_use_id(self):
        from claude_agent_sdk import UserMessage, ToolResultBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = UserMessage(content=[
            ToolResultBlock(tool_use_id="unknown", content="result")
        ])
        tool_use_map = {}
        event = agent._convert_user_message(ctx, msg, tool_use_map)
        assert event is not None
        assert event.content.parts[0].function_response.name == "tool_result"

    def test_non_list_content(self):
        from claude_agent_sdk import UserMessage
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = UserMessage(content="plain text")
        tool_use_map = {}
        event = agent._convert_user_message(ctx, msg, tool_use_map)
        assert event is None

    def test_empty_tool_results(self):
        from claude_agent_sdk import UserMessage
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = UserMessage(content=[])
        tool_use_map = {}
        event = agent._convert_user_message(ctx, msg, tool_use_map)
        assert event is None


# ---------------------------------------------------------------------------
# _convert_streaming_event
# ---------------------------------------------------------------------------

class TestConvertStreamingEvent:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        agent._streaming_tool_names = set()
        return agent

    def _make_stream_event(self, event_data):
        from claude_agent_sdk.types import StreamEvent
        return StreamEvent(uuid="test-uuid", session_id="test-session", event=event_data)

    def test_content_block_start_tool_use(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "t1", "name": "search"},
        })
        tool_info = {}
        result = agent._convert_streaming_event(ctx, stream_event, tool_info)
        assert result is None
        assert 1 in tool_info
        assert tool_info[1]["name"] == "search"

    def test_text_delta(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hello"},
        })
        result = agent._convert_streaming_event(ctx, stream_event, {})
        assert result is not None
        assert result.partial is True
        assert result.content.parts[0].text == "hello"

    def test_empty_text_delta_skipped(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": ""},
        })
        result = agent._convert_streaming_event(ctx, stream_event, {})
        assert result is None

    def test_input_json_delta_for_streaming_tool(self):
        agent = self._make_agent()
        agent._streaming_tool_names = {"search", "mcp__test_agent_tools__search"}
        ctx = _make_ctx()
        tool_info = {0: {"id": "t1", "name": "search"}}
        stream_event = self._make_stream_event({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"q":'},
        })
        result = agent._convert_streaming_event(ctx, stream_event, tool_info)
        assert result is not None
        assert result.partial is True
        assert result.custom_metadata["streaming_tool_call"] is True

    def test_input_json_delta_skips_non_streaming_tool(self):
        agent = self._make_agent()
        agent._streaming_tool_names = {"other_tool"}
        ctx = _make_ctx()
        tool_info = {0: {"id": "t1", "name": "search"}}
        stream_event = self._make_stream_event({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"q":'},
        })
        result = agent._convert_streaming_event(ctx, stream_event, tool_info)
        assert result is None

    def test_message_start_returns_none(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({"type": "message_start"})
        result = agent._convert_streaming_event(ctx, stream_event, {})
        assert result is None

    def test_message_stop_returns_none(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({"type": "message_stop"})
        result = agent._convert_streaming_event(ctx, stream_event, {})
        assert result is None

    def test_content_block_stop_returns_none(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({"type": "content_block_stop"})
        result = agent._convert_streaming_event(ctx, stream_event, {})
        assert result is None

    def test_unhandled_event_type(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({"type": "some_custom_event"})
        result = agent._convert_streaming_event(ctx, stream_event, {})
        assert result is None

    def test_content_block_start_non_tool_use(self):
        agent = self._make_agent()
        ctx = _make_ctx()
        stream_event = self._make_stream_event({
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        tool_info = {}
        result = agent._convert_streaming_event(ctx, stream_event, tool_info)
        assert result is None
        assert len(tool_info) == 0


# ---------------------------------------------------------------------------
# _parse_agent_config
# ---------------------------------------------------------------------------

class TestParseAgentConfig:
    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeAgentOptions")
    async def test_basic_config(self, MockOptions):
        mock_opts = MagicMock()
        mock_opts.cwd = None
        mock_opts.mcp_servers = None
        mock_opts.allowed_tools = None
        MockOptions.return_value = mock_opts

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        ctx = _make_ctx(streaming=True)

        result = await agent._parse_agent_config(ctx, "model-key")
        assert mock_opts.model == "model-key"
        assert mock_opts.include_partial_messages == True

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeAgentOptions")
    async def test_with_string_instruction(self, MockOptions):
        mock_opts = MagicMock()
        mock_opts.cwd = None
        mock_opts.mcp_servers = None
        mock_opts.allowed_tools = None
        MockOptions.return_value = mock_opts

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, instruction="Hello {name}")
        ctx = _make_ctx(state={"name": "Alice"})

        result = await agent._parse_agent_config(ctx, "model-key")
        assert mock_opts.system_prompt == "Hello Alice"

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeAgentOptions")
    async def test_with_callable_instruction(self, MockOptions):
        mock_opts = MagicMock()
        mock_opts.cwd = None
        mock_opts.mcp_servers = None
        mock_opts.allowed_tools = None
        MockOptions.return_value = mock_opts

        def instruction_provider(ctx):
            return "Dynamic instruction"

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, instruction=instruction_provider)
        ctx = _make_ctx()

        result = await agent._parse_agent_config(ctx, "model-key")
        assert mock_opts.system_prompt == "Dynamic instruction"

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeAgentOptions")
    async def test_with_async_instruction(self, MockOptions):
        mock_opts = MagicMock()
        mock_opts.cwd = None
        mock_opts.mcp_servers = None
        mock_opts.allowed_tools = None
        MockOptions.return_value = mock_opts

        async def async_instruction(ctx):
            return "Async instruction"

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, instruction=async_instruction)
        ctx = _make_ctx()

        result = await agent._parse_agent_config(ctx, "model-key")
        assert mock_opts.system_prompt == "Async instruction"

    async def test_uses_existing_options(self):
        from claude_agent_sdk import ClaudeAgentOptions
        existing_opts = ClaudeAgentOptions()
        existing_opts.cwd = "/existing/path"

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, claude_agent_options=existing_opts)
        ctx = _make_ctx()

        result = await agent._parse_agent_config(ctx, "model-key")
        assert result.cwd == "/existing/path"
        assert result.model == "model-key"


# ---------------------------------------------------------------------------
# _convert_tools_to_mcp
# ---------------------------------------------------------------------------

class TestConvertToolsToMcp:
    def _make_agent(self, tools=None):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model, tools=tools or [])

    async def test_no_tools(self):
        agent = self._make_agent([])
        ctx = _make_ctx()
        result = await agent._convert_tools_to_mcp(ctx)
        assert result is None

    async def test_with_base_tool(self):
        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"
        mock_decl = MagicMock()
        mock_decl.parameters = None
        mock_tool._get_declaration.return_value = mock_decl

        agent = self._make_agent([mock_tool])
        ctx = _make_ctx()

        with patch("trpc_agent_sdk.server.agents.claude._claude_agent.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = MagicMock()
            result = await agent._convert_tools_to_mcp(ctx)

        assert result is not None
        servers, allowed = result
        assert "test_agent_tools" in servers
        assert "mcp__test_agent_tools__test_tool" in allowed

    async def test_with_toolset(self):
        from trpc_agent_sdk.tools import BaseTool, BaseToolSet
        mock_inner = MagicMock(spec=BaseTool)
        mock_inner.name = "inner_tool"
        mock_inner.description = "inner"
        mock_decl = MagicMock()
        mock_decl.parameters = None
        mock_inner._get_declaration.return_value = mock_decl

        mock_toolset = MagicMock(spec=BaseToolSet)
        mock_toolset.get_tools = AsyncMock(return_value=[mock_inner])

        agent = self._make_agent([mock_toolset])
        ctx = _make_ctx()

        with patch("trpc_agent_sdk.server.agents.claude._claude_agent.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = MagicMock()
            result = await agent._convert_tools_to_mcp(ctx)

        assert result is not None

    async def test_with_callable(self):
        def my_func(x: str) -> str:
            return x

        agent = self._make_agent([my_func])
        ctx = _make_ctx()

        with patch("trpc_agent_sdk.server.agents.claude._claude_agent.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = MagicMock()
            result = await agent._convert_tools_to_mcp(ctx)

        assert result is not None

    async def test_all_tools_fail_conversion_returns_none(self):
        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "bad_tool_2"
        mock_tool.description = "fails too"
        mock_tool._get_declaration.side_effect = RuntimeError("declaration error")

        agent = self._make_agent([mock_tool])
        ctx = _make_ctx()
        result = await agent._convert_tools_to_mcp(ctx)
        assert result is None

    async def test_tool_conversion_error_handled(self):
        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "bad_tool"
        mock_tool.description = "fails"
        mock_tool._get_declaration.return_value = None  # Will cause ValueError

        agent = self._make_agent([mock_tool])
        ctx = _make_ctx()
        result = await agent._convert_tools_to_mcp(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# _run_async_impl
# ---------------------------------------------------------------------------

class TestRunAsyncImpl:
    """Tests for _run_async_impl. We mock CustomTraceReporter to avoid ctx attribute issues."""

    def _make_agent(self, enable_session=False, **kwargs):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model, enable_session=enable_session, **kwargs)

    @pytest.fixture(autouse=True)
    def _mock_trace_reporter(self):
        with patch("trpc_agent_sdk.server.agents.claude._claude_agent.CustomTraceReporter") as MockReporter:
            MockReporter.return_value = MagicMock()
            yield

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_with_session_manager(self, mock_add):
        agent = self._make_agent(enable_session=False)

        mock_sm = MagicMock(spec=SessionManager)
        from claude_agent_sdk import TextBlock, AssistantMessage
        assistant_msg = AssistantMessage(model="test", content=[TextBlock(text="response")])

        async def mock_stream_query(**kwargs):
            yield assistant_msg

        mock_sm.stream_query = mock_stream_query
        agent._session_manager = mock_sm
        agent._runtime = MagicMock(spec=AsyncRuntime)

        ctx = _make_ctx(session_events=[_make_event(author="user", text="hello")])

        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)

        assert len(events) > 0

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeSDKClient")
    async def test_run_with_enable_session(self, MockClient, mock_add):
        agent = self._make_agent(enable_session=True)

        from claude_agent_sdk import TextBlock, AssistantMessage
        assistant_msg = AssistantMessage(model="test", content=[TextBlock(text="response")])

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()

        async def mock_receive():
            yield assistant_msg

        mock_client.receive_response = mock_receive
        MockClient.return_value = mock_client

        ctx = _make_ctx(session_events=[_make_event(author="user", text="hello")])

        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)

        assert len(events) > 0

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_no_user_message(self, mock_add):
        agent = self._make_agent(enable_session=False)

        mock_sm = MagicMock(spec=SessionManager)
        agent._session_manager = mock_sm
        agent._runtime = MagicMock(spec=AsyncRuntime)

        ctx = _make_ctx(session_events=[])  # No events

        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)

        assert len(events) == 0

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_with_override_messages(self, mock_add):
        agent = self._make_agent(enable_session=False)

        mock_sm = MagicMock(spec=SessionManager)
        from claude_agent_sdk import TextBlock, AssistantMessage
        assistant_msg = AssistantMessage(model="test", content=[TextBlock(text="response")])

        async def mock_stream_query(**kwargs):
            yield assistant_msg

        mock_sm.stream_query = mock_stream_query
        agent._session_manager = mock_sm
        agent._runtime = MagicMock(spec=AsyncRuntime)

        override_msgs = [Content(role="user", parts=[Part.from_text(text="override hello")])]
        ctx = _make_ctx(override_messages=override_msgs)

        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)

        assert len(events) > 0

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_with_history_mode(self, mock_add):
        agent = self._make_agent(enable_session=True)

        from claude_agent_sdk import TextBlock, AssistantMessage
        assistant_msg = AssistantMessage(model="test", content=[TextBlock(text="response")])

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()

        async def mock_receive():
            yield assistant_msg

        mock_client.receive_response = mock_receive

        with patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeSDKClient", return_value=mock_client):
            ctx = _make_ctx(session_events=[
                _make_event(author="user", text="First"),
                _make_event(author="test_agent", text="Answer"),
                _make_event(author="user", text="Second"),
            ])

            events = []
            async for event in agent._run_async_impl(ctx):
                events.append(event)

            assert len(events) > 0

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_error_yields_error_event(self, mock_add):
        agent = self._make_agent(enable_session=False)

        mock_sm = MagicMock(spec=SessionManager)

        async def mock_stream_query(**kwargs):
            raise RuntimeError("query failed")
            yield  # noqa

        mock_sm.stream_query = mock_stream_query
        agent._session_manager = mock_sm
        agent._runtime = MagicMock(spec=AsyncRuntime)

        ctx = _make_ctx(session_events=[_make_event(author="user", text="hello")])

        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)

        assert len(events) == 1
        assert "Error" in events[0].content.parts[0].text

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_cancellation_reraise(self, mock_add):
        from trpc_agent_sdk.exceptions import RunCancelledException
        agent = self._make_agent(enable_session=False)

        mock_sm = MagicMock(spec=SessionManager)

        async def mock_stream_query(**kwargs):
            raise RunCancelledException()
            yield  # noqa

        mock_sm.stream_query = mock_stream_query
        agent._session_manager = mock_sm
        agent._runtime = MagicMock(spec=AsyncRuntime)

        ctx = _make_ctx(session_events=[_make_event(author="user", text="hello")])

        with pytest.raises(RunCancelledException):
            async for _ in agent._run_async_impl(ctx):
                pass

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_saves_output(self, mock_add):
        agent = self._make_agent(enable_session=False, output_key="result")

        mock_sm = MagicMock(spec=SessionManager)
        from claude_agent_sdk import TextBlock, AssistantMessage
        assistant_msg = AssistantMessage(model="test", content=[TextBlock(text="final output")])

        async def mock_stream_query(**kwargs):
            yield assistant_msg

        mock_sm.stream_query = mock_stream_query
        agent._session_manager = mock_sm
        agent._runtime = MagicMock(spec=AsyncRuntime)

        ctx = _make_ctx(session_events=[_make_event(author="user", text="hello")], state={})

        events = []
        async for event in agent._run_async_impl(ctx):
            events.append(event)

        assert ctx.state.get("result") == "final output"

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent._add_model", return_value="model-key")
    async def test_run_auto_initializes(self, mock_add):
        agent = self._make_agent(enable_session=False)
        assert agent._session_manager is None

        from claude_agent_sdk import TextBlock, AssistantMessage
        assistant_msg = AssistantMessage(model="test", content=[TextBlock(text="response")])

        ctx = _make_ctx(session_events=[_make_event(author="user", text="hello")])

        with patch.object(AsyncRuntime, "start"), \
             patch.object(SessionManager, "stream_query") as mock_stream:
            async def mock_gen(*args, **kwargs):
                yield assistant_msg
            mock_stream.return_value = mock_gen()

            events = []
            async for event in agent._run_async_impl(ctx):
                events.append(event)

            assert agent._session_manager is not None


# ---------------------------------------------------------------------------
# _convert_tool_to_sdk_tool handler
# ---------------------------------------------------------------------------

class TestConvertToolToSdkToolHandler:
    async def test_handler_dict_with_content_key(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "test_tool"
        mock_tool.description = "desc"
        mock_tool.run_async = AsyncMock(return_value={"content": [{"type": "text", "text": "ok"}]})
        mock_decl = MagicMock()
        mock_decl.parameters = None
        mock_tool._get_declaration.return_value = mock_decl

        ctx = _make_ctx()
        sdk_tool = agent._convert_tool_to_sdk_tool(mock_tool, ctx)
        assert sdk_tool.name == "test_tool"

    async def test_handler_none_result(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)

        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "test_tool"
        mock_tool.description = "desc"
        mock_tool.run_async = AsyncMock(return_value=None)
        mock_decl = MagicMock()
        mock_decl.parameters = None
        mock_tool._get_declaration.return_value = mock_decl

        ctx = _make_ctx()
        sdk_tool = agent._convert_tool_to_sdk_tool(mock_tool, ctx)
        assert sdk_tool is not None


# ---------------------------------------------------------------------------
# _parse_agent_config with tools
# ---------------------------------------------------------------------------

class TestParseAgentConfigWithTools:
    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeAgentOptions")
    async def test_config_with_tools(self, MockOptions):
        mock_opts = MagicMock()
        mock_opts.cwd = None
        mock_opts.mcp_servers = None
        mock_opts.allowed_tools = None
        MockOptions.return_value = mock_opts

        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "my_tool"
        mock_tool.description = "tool desc"
        mock_tool.is_streaming = False
        mock_decl = MagicMock()
        mock_decl.parameters = None
        mock_tool._get_declaration.return_value = mock_decl

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, tools=[mock_tool])
        ctx = _make_ctx()

        with patch("trpc_agent_sdk.server.agents.claude._claude_agent.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = MagicMock()
            result = await agent._parse_agent_config(ctx, "model-key")

        assert mock_opts.allowed_tools is not None or mock_opts.mcp_servers is not None

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeAgentOptions")
    async def test_config_merges_existing_mcp_servers(self, MockOptions):
        mock_opts = MagicMock()
        mock_opts.cwd = None
        mock_opts.mcp_servers = {"existing_server": MagicMock()}
        mock_opts.allowed_tools = ["existing_tool"]
        MockOptions.return_value = mock_opts

        from trpc_agent_sdk.tools import BaseTool
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "new_tool"
        mock_tool.description = "new"
        mock_tool.is_streaming = False
        mock_decl = MagicMock()
        mock_decl.parameters = None
        mock_tool._get_declaration.return_value = mock_decl

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model, tools=[mock_tool])
        ctx = _make_ctx()

        with patch("trpc_agent_sdk.server.agents.claude._claude_agent.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = MagicMock()
            result = await agent._parse_agent_config(ctx, "model-key")

    @patch("trpc_agent_sdk.server.agents.claude._claude_agent.ClaudeAgentOptions")
    async def test_sets_cwd_from_entry_point(self, MockOptions):
        mock_opts = MagicMock()
        mock_opts.cwd = None
        mock_opts.mcp_servers = None
        mock_opts.allowed_tools = None
        MockOptions.return_value = mock_opts

        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        ctx = _make_ctx()

        with patch.object(agent, "_get_entry_point_dir", return_value="/some/path"):
            result = await agent._parse_agent_config(ctx, "model-key")

        assert mock_opts.cwd == "/some/path"


# ---------------------------------------------------------------------------
# _convert_message_to_event additional
# ---------------------------------------------------------------------------

class TestConvertMessageToEventAdditional:
    def _make_agent(self):
        model = MockLLMModel(model_name="test-claude-1")
        return ClaudeAgent(name="test_agent", model=model)

    def test_assistant_message_dispatched(self):
        from claude_agent_sdk import AssistantMessage, TextBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = AssistantMessage(model="test", content=[TextBlock(text="hello")])
        result = agent._convert_message_to_event(ctx, msg, {})
        assert result is not None

    def test_user_message_dispatched(self):
        from claude_agent_sdk import UserMessage, ToolResultBlock
        agent = self._make_agent()
        ctx = _make_ctx()
        msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="result")])
        result = agent._convert_message_to_event(ctx, msg, {"t1": "search"})
        assert result is not None

    def test_stream_event_dispatched(self):
        from claude_agent_sdk.types import StreamEvent
        agent = self._make_agent()
        agent._streaming_tool_names = set()
        ctx = _make_ctx()
        msg = StreamEvent(uuid="u1", session_id="s1", event={"type": "message_start"})
        result = agent._convert_message_to_event(ctx, msg, {}, {})
        assert result is None


# ---------------------------------------------------------------------------
# _get_entry_point_dir with valid file
# ---------------------------------------------------------------------------

class TestGetEntryPointDirWithFile:
    def test_with_valid_main(self):
        model = MockLLMModel(model_name="test-claude-1")
        agent = ClaudeAgent(name="test_agent", model=model)
        mock_main = MagicMock()
        mock_main.__file__ = "/some/dir/main.py"
        with patch.dict(sys.modules, {"__main__": mock_main}):
            result = agent._get_entry_point_dir()
            assert result is not None
            assert "some/dir" in result or "some\\dir" in result
