# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for AgUiAgent class."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from ag_ui.core import RunAgentInput, Tool as AGUITool
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.configs import RunConfig as TRPCRunConfig
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import LongRunningFunctionTool

from trpc_agent_sdk.sessions import Session

from trpc_agent_sdk.server.ag_ui._core._agui_agent import AgUiAgent
from trpc_agent_sdk.server.ag_ui._core._client_proxy_toolset import ClientProxyToolset
from trpc_agent_sdk.server.ag_ui._core._execution_state import ExecutionState
from trpc_agent_sdk.server.ag_ui._core._feed_back_content import AgUiUserFeedBack
from trpc_agent_sdk.server.ag_ui._core._session_manager import SessionManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_session_manager():
    SessionManager.reset_instance()
    yield
    SessionManager.reset_instance()


@pytest.fixture
def mock_agent():
    agent = Mock(spec=BaseAgent)
    agent.name = "test_agent"
    agent.tools = []
    agent.sub_agents = []
    agent.model_copy = Mock(return_value=agent)
    return agent


@pytest.fixture
def agui_agent(mock_agent):
    return AgUiAgent(
        trpc_agent=mock_agent,
        app_name="test_app",
        user_id="test_user",
        auto_cleanup=False,
    )


def _make_input(*, thread_id="thread-1", run_id="run-1", messages=None, state=None, tools=None):
    """Helper to create a mock RunAgentInput."""
    mock_input = Mock(spec=RunAgentInput)
    mock_input.thread_id = thread_id
    mock_input.run_id = run_id
    mock_input.messages = messages or []
    mock_input.state = state or {}
    mock_input.tools = tools or []
    return mock_input


def _make_user_message(content="hello"):
    msg = Mock()
    msg.role = "user"
    msg.content = content
    msg.tool_calls = None
    return msg


def _make_tool_message(content='{"ok": true}', tool_call_id="tc-1"):
    msg = Mock()
    msg.role = "tool"
    msg.content = content
    msg.tool_call_id = tool_call_id
    msg.tool_calls = None
    return msg


def _make_assistant_message(content="sure", tool_calls=None):
    msg = Mock()
    msg.role = "assistant"
    msg.content = content
    msg.tool_calls = tool_calls or []
    return msg


def _make_text_part(text):
    """Create a real Part with just text for pydantic-validated Content."""
    from trpc_agent_sdk import types
    return types.Part(text=text)


def _make_tool_call(tc_id="tc-1", name="my_tool"):
    tc = Mock()
    tc.id = tc_id
    tc.function = Mock()
    tc.function.name = name
    return tc


# ---------------------------------------------------------------------------
# TestAgUiAgentInit
# ---------------------------------------------------------------------------


class TestAgUiAgentInit:
    def test_init_with_required_params_only(self, mock_agent):
        agent = AgUiAgent(trpc_agent=mock_agent, auto_cleanup=False)
        assert agent._trpc_agent is mock_agent
        assert agent._static_app_name is None
        assert agent._static_user_id is None

    def test_raises_for_both_app_name_and_extractor(self, mock_agent):
        with pytest.raises(ValueError, match="Cannot specify both 'app_name' and 'app_name_extractor'"):
            AgUiAgent(
                trpc_agent=mock_agent,
                app_name="app",
                app_name_extractor=lambda inp: "other",
                auto_cleanup=False,
            )

    def test_raises_for_both_user_id_and_extractor(self, mock_agent):
        with pytest.raises(ValueError, match="Cannot specify both 'user_id' and 'user_id_extractor'"):
            AgUiAgent(
                trpc_agent=mock_agent,
                user_id="u",
                user_id_extractor=lambda inp: "other",
                auto_cleanup=False,
            )

    def test_init_with_custom_services(self, mock_agent):
        session_svc = Mock(spec=InMemorySessionService)
        memory_svc = Mock(spec=InMemoryMemoryService)

        agent = AgUiAgent(
            trpc_agent=mock_agent,
            session_service=session_svc,
            memory_service=memory_svc,
            auto_cleanup=False,
        )
        assert agent._memory_service is memory_svc

    def test_init_stores_timeout_values(self, mock_agent):
        agent = AgUiAgent(
            trpc_agent=mock_agent,
            execution_timeout_seconds=123,
            tool_timeout_seconds=45,
            max_concurrent_executions=7,
            cancel_wait_timeout=5.0,
            auto_cleanup=False,
        )
        assert agent._execution_timeout == 123
        assert agent._tool_timeout == 45
        assert agent._max_concurrent == 7
        assert agent._cancel_wait_timeout == 5.0


# ---------------------------------------------------------------------------
# TestGetAppName
# ---------------------------------------------------------------------------


class TestGetAppName:
    def test_returns_static_app_name(self, mock_agent):
        agent = AgUiAgent(trpc_agent=mock_agent, app_name="my_app", auto_cleanup=False)
        result = agent.get_app_name(_make_input())
        assert result == "my_app"

    def test_calls_app_name_extractor(self, mock_agent):
        extractor = Mock(return_value="extracted_app")
        agent = AgUiAgent(trpc_agent=mock_agent, app_name_extractor=extractor, auto_cleanup=False)

        inp = _make_input()
        result = agent.get_app_name(inp)

        assert result == "extracted_app"
        extractor.assert_called_once_with(inp)

    def test_defaults_to_agent_name(self, mock_agent):
        mock_agent.name = "agent_foo"
        agent = AgUiAgent(trpc_agent=mock_agent, auto_cleanup=False)

        result = agent.get_app_name(_make_input())
        assert result == "agent_foo"


# ---------------------------------------------------------------------------
# TestGetUserId
# ---------------------------------------------------------------------------


class TestGetUserId:
    def test_returns_static_user_id(self, mock_agent):
        agent = AgUiAgent(trpc_agent=mock_agent, user_id="uid-42", auto_cleanup=False)
        result = agent.get_user_id(_make_input())
        assert result == "uid-42"

    def test_calls_user_id_extractor(self, mock_agent):
        extractor = Mock(return_value="extracted_uid")
        agent = AgUiAgent(trpc_agent=mock_agent, user_id_extractor=extractor, auto_cleanup=False)

        inp = _make_input()
        result = agent.get_user_id(inp)

        assert result == "extracted_uid"
        extractor.assert_called_once_with(inp)

    def test_defaults_to_thread_user_prefix(self, mock_agent):
        agent = AgUiAgent(trpc_agent=mock_agent, auto_cleanup=False)
        inp = _make_input(thread_id="t-99")

        result = agent.get_user_id(inp)
        assert result == "thread_user_t-99"


# ---------------------------------------------------------------------------
# TestIsToolResultSubmission
# ---------------------------------------------------------------------------


class TestIsToolResultSubmission:
    def test_true_when_last_message_is_tool(self, agui_agent):
        inp = _make_input(messages=[_make_user_message(), _make_tool_message()])
        assert agui_agent._is_tool_result_submission(inp) is True

    def test_false_when_no_messages(self, agui_agent):
        inp = _make_input(messages=[])
        assert agui_agent._is_tool_result_submission(inp) is False

    def test_false_when_last_message_is_user(self, agui_agent):
        inp = _make_input(messages=[_make_tool_message(), _make_user_message()])
        assert agui_agent._is_tool_result_submission(inp) is False


# ---------------------------------------------------------------------------
# TestExtractToolResults
# ---------------------------------------------------------------------------


class TestExtractToolResults:
    async def test_extracts_most_recent_tool_message(self, agui_agent):
        tool_msg = _make_tool_message(content='{"result": "ok"}', tool_call_id="tc-5")
        inp = _make_input(messages=[_make_user_message(), tool_msg])

        results = await agui_agent._extract_tool_results(inp)

        assert len(results) == 1
        assert results[0]["message"] is tool_msg
        assert results[0]["tool_name"] == "unknown"

    async def test_returns_empty_list_when_no_tool_messages(self, agui_agent):
        inp = _make_input(messages=[_make_user_message()])
        results = await agui_agent._extract_tool_results(inp)
        assert results == []

    async def test_maps_tool_call_id_to_name(self, agui_agent):
        tc = _make_tool_call(tc_id="tc-10", name="search")
        assistant_msg = _make_assistant_message(tool_calls=[tc])
        tool_msg = _make_tool_message(content='{"data": []}', tool_call_id="tc-10")

        inp = _make_input(messages=[assistant_msg, tool_msg])
        results = await agui_agent._extract_tool_results(inp)

        assert len(results) == 1
        assert results[0]["tool_name"] == "search"
        assert results[0]["message"] is tool_msg

    async def test_returns_empty_list_when_messages_empty(self, agui_agent):
        inp = _make_input(messages=[])
        results = await agui_agent._extract_tool_results(inp)
        assert results == []


# ---------------------------------------------------------------------------
# TestConvertLatestMessage
# ---------------------------------------------------------------------------


class TestConvertLatestMessage:
    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts")
    async def test_converts_user_message(self, mock_convert, agui_agent):
        mock_convert.return_value = [_make_text_part("hello")]
        inp = _make_input(messages=[_make_user_message("hello")])

        result = await agui_agent._convert_latest_message(inp)

        assert result is not None
        assert result.role == "user"
        assert len(result.parts) == 1
        mock_convert.assert_called_once()

    async def test_returns_none_when_no_messages(self, agui_agent):
        inp = _make_input(messages=[])
        result = await agui_agent._convert_latest_message(inp)
        assert result is None

    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts")
    async def test_skips_non_user_messages(self, mock_convert, agui_agent):
        mock_convert.return_value = [_make_text_part("hi")]
        user_msg = _make_user_message("hi")
        assistant_msg = _make_assistant_message("reply")

        inp = _make_input(messages=[user_msg, assistant_msg])
        result = await agui_agent._convert_latest_message(inp)

        assert result is not None
        assert result.role == "user"

    async def test_returns_none_when_only_tool_messages(self, agui_agent):
        inp = _make_input(messages=[_make_tool_message()])
        result = await agui_agent._convert_latest_message(inp)
        assert result is None

    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts")
    async def test_returns_none_when_convert_returns_empty(self, mock_convert, agui_agent):
        mock_convert.return_value = []
        inp = _make_input(messages=[_make_user_message("hi")])

        result = await agui_agent._convert_latest_message(inp)
        assert result is None


# ---------------------------------------------------------------------------
# TestExtractLongRunningToolNames
# ---------------------------------------------------------------------------


class TestExtractLongRunningToolNames:
    def test_extracts_from_long_running_function_tool(self, agui_agent):
        lrt = Mock(spec=LongRunningFunctionTool)
        lrt.name = "slow_tool"

        agent = Mock(spec=BaseAgent)
        agent.tools = [lrt]

        result = agui_agent._extract_long_running_tool_names(agent)
        assert result == ["slow_tool"]

    def test_extracts_from_client_proxy_toolset(self, agui_agent):
        ag_ui_tool_1 = Mock(spec=AGUITool)
        ag_ui_tool_1.name = "proxy_a"
        ag_ui_tool_2 = Mock(spec=AGUITool)
        ag_ui_tool_2.name = "proxy_b"

        toolset = Mock(spec=ClientProxyToolset)
        toolset.ag_ui_tools = [ag_ui_tool_1, ag_ui_tool_2]

        agent = Mock(spec=BaseAgent)
        agent.tools = [toolset]

        result = agui_agent._extract_long_running_tool_names(agent)
        assert result == ["proxy_a", "proxy_b"]

    def test_returns_empty_for_no_tools(self, agui_agent):
        agent = Mock(spec=BaseAgent)
        agent.tools = []
        assert agui_agent._extract_long_running_tool_names(agent) == []

    def test_returns_empty_when_tools_is_none(self, agui_agent):
        agent = Mock(spec=BaseAgent)
        agent.tools = None
        assert agui_agent._extract_long_running_tool_names(agent) == []

    def test_handles_single_tool_not_list(self, agui_agent):
        lrt = Mock(spec=LongRunningFunctionTool)
        lrt.name = "solo_tool"

        agent = Mock(spec=BaseAgent)
        agent.tools = lrt  # not a list

        result = agui_agent._extract_long_running_tool_names(agent)
        assert result == ["solo_tool"]

    def test_mixed_tools(self, agui_agent):
        lrt = Mock(spec=LongRunningFunctionTool)
        lrt.name = "long_one"

        regular_tool = Mock()

        ag_ui_tool = Mock(spec=AGUITool)
        ag_ui_tool.name = "proxy_tool"
        toolset = Mock(spec=ClientProxyToolset)
        toolset.ag_ui_tools = [ag_ui_tool]

        agent = Mock(spec=BaseAgent)
        agent.tools = [lrt, regular_tool, toolset]

        result = agui_agent._extract_long_running_tool_names(agent)
        assert "long_one" in result
        assert "proxy_tool" in result
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestDefaultRunConfig
# ---------------------------------------------------------------------------


class TestDefaultRunConfig:
    def test_returns_run_config_with_streaming(self, agui_agent):
        inp = _make_input()
        config = agui_agent._default_run_config(inp)

        assert isinstance(config, TRPCRunConfig)
        assert config.streaming is True


# ---------------------------------------------------------------------------
# TestGetSessionMetadata
# ---------------------------------------------------------------------------


class TestGetSessionMetadata:
    def test_returns_from_cache(self, agui_agent):
        agui_agent._session_lookup_cache["sess-1"] = {
            "app_name": "cached_app",
            "user_id": "cached_user",
        }

        result = agui_agent._get_session_metadata("sess-1")

        assert result == {"app_name": "cached_app", "user_id": "cached_user"}

    def test_falls_back_to_linear_search(self, agui_agent):
        agui_agent._session_manager._user_sessions = {
            "user-A": {"myapp:sess-42"},
        }

        result = agui_agent._get_session_metadata("sess-42")

        assert result is not None
        assert result["app_name"] == "myapp"
        assert result["user_id"] == "user-A"
        # Should be cached now
        assert "sess-42" in agui_agent._session_lookup_cache

    def test_returns_none_when_not_found(self, agui_agent):
        agui_agent._session_manager._user_sessions = {}
        result = agui_agent._get_session_metadata("nonexistent")
        assert result is None

    def test_returns_none_on_exception(self, agui_agent):
        agui_agent._session_manager._user_sessions = Mock(
            items=Mock(side_effect=RuntimeError("boom"))
        )
        result = agui_agent._get_session_metadata("sess-err")
        assert result is None


# ---------------------------------------------------------------------------
# TestCancelRun
# ---------------------------------------------------------------------------


class TestCancelRun:
    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.cancel")
    async def test_cancels_active_run(self, mock_cancel_module, agui_agent):
        cleanup_event = asyncio.Event()
        cleanup_event.set()
        mock_cancel_module.cancel_run = AsyncMock(return_value=cleanup_event)

        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)

        result = await agui_agent.cancel_run("sess-1", "test_app", "test_user")

        assert result is True
        mock_cancel_module.cancel_run.assert_awaited_once_with("test_app", "test_user", "sess-1")
        agui_agent._session_manager.set_state_value.assert_awaited_once_with(
            session_id="sess-1",
            app_name="test_app",
            user_id="test_user",
            key="pending_tool_calls",
            value=[],
        )

    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.cancel")
    async def test_handles_no_active_run(self, mock_cancel_module, agui_agent):
        mock_cancel_module.cancel_run = AsyncMock(return_value=None)

        result = await agui_agent.cancel_run("sess-1", "test_app", "test_user")

        assert result is False

    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.cancel")
    async def test_clears_pending_tool_calls(self, mock_cancel_module, agui_agent):
        cleanup_event = asyncio.Event()
        cleanup_event.set()
        mock_cancel_module.cancel_run = AsyncMock(return_value=cleanup_event)

        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)

        await agui_agent.cancel_run("sess-1", "app", "user")

        agui_agent._session_manager.set_state_value.assert_awaited_once_with(
            session_id="sess-1",
            app_name="app",
            user_id="user",
            key="pending_tool_calls",
            value=[],
        )

    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.cancel")
    async def test_cancels_execution_state(self, mock_cancel_module, agui_agent):
        cleanup_event = asyncio.Event()
        cleanup_event.set()
        mock_cancel_module.cancel_run = AsyncMock(return_value=cleanup_event)
        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)

        mock_execution = AsyncMock(spec=ExecutionState)
        mock_execution.cancel = AsyncMock()
        agui_agent._active_executions["sess-1"] = mock_execution

        result = await agui_agent.cancel_run("sess-1", "app", "user")

        assert result is True
        mock_execution.cancel.assert_awaited_once()
        assert "sess-1" not in agui_agent._active_executions

    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.cancel")
    async def test_handles_timeout_during_cancel_wait(self, mock_cancel_module, agui_agent):
        never_set_event = asyncio.Event()
        mock_cancel_module.cancel_run = AsyncMock(return_value=never_set_event)
        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)
        agui_agent._cancel_wait_timeout = 0.01

        result = await agui_agent.cancel_run("sess-1", "app", "user")
        assert result is True


# ---------------------------------------------------------------------------
# TestExecuteUserFeedbackHandler
# ---------------------------------------------------------------------------


class TestExecuteUserFeedbackHandler:
    async def test_no_handler_returns_original_message(self, agui_agent):
        agui_agent._user_feedback_handler = None

        result = await agui_agent._execute_user_feedback_handler(
            tool_name="t", tool_message="original", thread_id="th", app_name="app", user_id="u"
        )
        assert result == "original"

    async def test_handler_modifies_tool_message(self, mock_agent):
        async def handler(feedback: AgUiUserFeedBack):
            feedback.tool_message = "modified"

        agent = AgUiAgent(
            trpc_agent=mock_agent,
            app_name="test_app",
            user_id="test_user",
            user_feedback_handler=handler,
            auto_cleanup=False,
        )

        real_session = Session(id="th-1", app_name="test_app", user_id="test_user", save_key="k", state={})
        agent._session_manager._session_service.get_session = AsyncMock(return_value=real_session)

        result = await agent._execute_user_feedback_handler(
            tool_name="tool_x", tool_message="original", thread_id="th-1", app_name="test_app", user_id="test_user"
        )
        assert result == "modified"

    async def test_handler_marks_session_modified_triggers_update(self, mock_agent):
        async def handler(feedback: AgUiUserFeedBack):
            feedback.mark_session_modified()

        agent = AgUiAgent(
            trpc_agent=mock_agent,
            app_name="test_app",
            user_id="test_user",
            user_feedback_handler=handler,
            auto_cleanup=False,
        )

        real_session = Session(id="th-1", app_name="test_app", user_id="test_user", save_key="k", state={})
        agent._session_manager._session_service.get_session = AsyncMock(return_value=real_session)
        agent._session_manager._session_service.update_session = AsyncMock()

        await agent._execute_user_feedback_handler(
            tool_name="t", tool_message="msg", thread_id="th-1", app_name="test_app", user_id="test_user"
        )

        agent._session_manager._session_service.update_session.assert_awaited_once_with(real_session)

    async def test_handler_exception_returns_original_message(self, mock_agent):
        async def handler(feedback: AgUiUserFeedBack):
            raise RuntimeError("handler broke")

        agent = AgUiAgent(
            trpc_agent=mock_agent,
            app_name="test_app",
            user_id="test_user",
            user_feedback_handler=handler,
            auto_cleanup=False,
        )

        real_session = Session(id="th-1", app_name="test_app", user_id="test_user", save_key="k", state={})
        agent._session_manager._session_service.get_session = AsyncMock(return_value=real_session)

        result = await agent._execute_user_feedback_handler(
            tool_name="t", tool_message="original", thread_id="th-1", app_name="test_app", user_id="test_user"
        )
        assert result == "original"

    async def test_session_not_found_returns_original_message(self, mock_agent):
        async def handler(feedback: AgUiUserFeedBack):
            feedback.tool_message = "should not reach"

        agent = AgUiAgent(
            trpc_agent=mock_agent,
            app_name="test_app",
            user_id="test_user",
            user_feedback_handler=handler,
            auto_cleanup=False,
        )

        agent._session_manager._session_service.get_session = AsyncMock(return_value=None)

        result = await agent._execute_user_feedback_handler(
            tool_name="t", tool_message="original", thread_id="th-1", app_name="test_app", user_id="test_user"
        )
        assert result == "original"


# ---------------------------------------------------------------------------
# TestCleanupStaleExecutions
# ---------------------------------------------------------------------------


class TestCleanupStaleExecutions:
    async def test_removes_stale_executions(self, agui_agent):
        stale_exec = AsyncMock(spec=ExecutionState)
        stale_exec.is_stale = Mock(return_value=True)
        stale_exec.cancel = AsyncMock()

        agui_agent._active_executions["thread-stale"] = stale_exec

        await agui_agent._cleanup_stale_executions()

        assert "thread-stale" not in agui_agent._active_executions
        stale_exec.cancel.assert_awaited_once()

    async def test_keeps_fresh_executions(self, agui_agent):
        fresh_exec = AsyncMock(spec=ExecutionState)
        fresh_exec.is_stale = Mock(return_value=False)

        agui_agent._active_executions["thread-fresh"] = fresh_exec

        await agui_agent._cleanup_stale_executions()

        assert "thread-fresh" in agui_agent._active_executions

    async def test_mixed_stale_and_fresh(self, agui_agent):
        stale = AsyncMock(spec=ExecutionState)
        stale.is_stale = Mock(return_value=True)
        stale.cancel = AsyncMock()

        fresh = AsyncMock(spec=ExecutionState)
        fresh.is_stale = Mock(return_value=False)

        agui_agent._active_executions["stale-1"] = stale
        agui_agent._active_executions["fresh-1"] = fresh

        await agui_agent._cleanup_stale_executions()

        assert "stale-1" not in agui_agent._active_executions
        assert "fresh-1" in agui_agent._active_executions
        stale.cancel.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------


class TestClose:
    async def test_cancels_all_active_executions(self, agui_agent):
        exec_1 = AsyncMock(spec=ExecutionState)
        exec_1.cancel = AsyncMock()
        exec_2 = AsyncMock(spec=ExecutionState)
        exec_2.cancel = AsyncMock()

        agui_agent._active_executions["t1"] = exec_1
        agui_agent._active_executions["t2"] = exec_2

        agui_agent._session_manager.stop_cleanup_task = AsyncMock()

        await agui_agent.close()

        exec_1.cancel.assert_awaited_once()
        exec_2.cancel.assert_awaited_once()
        assert len(agui_agent._active_executions) == 0

    async def test_stops_cleanup_task(self, agui_agent):
        agui_agent._session_manager.stop_cleanup_task = AsyncMock()

        await agui_agent.close()

        agui_agent._session_manager.stop_cleanup_task.assert_awaited_once()

    async def test_clears_session_lookup_cache(self, agui_agent):
        agui_agent._session_lookup_cache["s1"] = {"app_name": "a", "user_id": "u"}
        agui_agent._session_manager.stop_cleanup_task = AsyncMock()

        await agui_agent.close()

        assert len(agui_agent._session_lookup_cache) == 0


# ---------------------------------------------------------------------------
# TestEnsureSessionExists
# ---------------------------------------------------------------------------


class TestEnsureSessionExists:
    async def test_creates_session_and_populates_cache(self, agui_agent):
        mock_session = Mock()
        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=mock_session)

        result = await agui_agent._ensure_session_exists("app", "user", "sess-1", {"key": "val"})

        assert result is mock_session
        agui_agent._session_manager.get_or_create_session.assert_awaited_once_with(
            session_id="sess-1",
            app_name="app",
            user_id="user",
            initial_state={"key": "val"},
        )
        assert agui_agent._session_lookup_cache["sess-1"] == {"app_name": "app", "user_id": "user"}

    async def test_propagates_exception(self, agui_agent):
        agui_agent._session_manager.get_or_create_session = AsyncMock(
            side_effect=RuntimeError("db error")
        )

        with pytest.raises(RuntimeError, match="db error"):
            await agui_agent._ensure_session_exists("app", "user", "sess-err", {})


# ---------------------------------------------------------------------------
# TestCreateRunner
# ---------------------------------------------------------------------------


class TestCreateRunner:
    @patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner")
    def test_creates_runner_with_correct_params(self, mock_runner_cls, agui_agent, mock_agent):
        agui_agent._create_runner(mock_agent, "user-1", "app-1")

        mock_runner_cls.assert_called_once_with(
            app_name="app-1",
            agent=mock_agent,
            session_service=agui_agent._session_manager._session_service,
            memory_service=agui_agent._memory_service,
        )


# ---------------------------------------------------------------------------
# TestDefaultAppExtractor
# ---------------------------------------------------------------------------


class TestDefaultAppExtractor:
    def test_returns_agent_name(self, mock_agent):
        mock_agent.name = "my_special_agent"
        agent = AgUiAgent(trpc_agent=mock_agent, auto_cleanup=False)
        result = agent._default_app_extractor(_make_input())
        assert result == "my_special_agent"

    def test_returns_fallback_on_exception(self, mock_agent):
        type(mock_agent).name = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        agent = AgUiAgent.__new__(AgUiAgent)
        agent._trpc_agent = mock_agent
        result = agent._default_app_extractor(_make_input())
        assert result == "AG-UI TRPC Agent"


# ---------------------------------------------------------------------------
# TestAddPendingToolCallWithContext
# ---------------------------------------------------------------------------


class TestAddPendingToolCallWithContext:
    async def test_adds_tool_call_to_pending_list(self, agui_agent):
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=[])
        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)

        await agui_agent._add_pending_tool_call_with_context("sess-1", "tc-1", "app", "user")

        agui_agent._session_manager.set_state_value.assert_awaited_once_with(
            session_id="sess-1", app_name="app", user_id="user",
            key="pending_tool_calls", value=["tc-1"],
        )

    async def test_does_not_add_duplicate(self, agui_agent):
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=["tc-1"])
        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)

        await agui_agent._add_pending_tool_call_with_context("sess-1", "tc-1", "app", "user")

        agui_agent._session_manager.set_state_value.assert_not_awaited()

    async def test_handles_exception(self, agui_agent):
        agui_agent._session_manager.get_state_value = AsyncMock(side_effect=RuntimeError("db fail"))

        # Should not raise
        await agui_agent._add_pending_tool_call_with_context("sess-1", "tc-1", "app", "user")


# ---------------------------------------------------------------------------
# TestRemovePendingToolCall
# ---------------------------------------------------------------------------


class TestRemovePendingToolCall:
    async def test_removes_tool_call(self, agui_agent):
        agui_agent._session_lookup_cache["sess-1"] = {"app_name": "app", "user_id": "user"}
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=["tc-1", "tc-2"])
        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)

        await agui_agent._remove_pending_tool_call("sess-1", "tc-1")

        agui_agent._session_manager.set_state_value.assert_awaited_once_with(
            session_id="sess-1", app_name="app", user_id="user",
            key="pending_tool_calls", value=["tc-2"],
        )

    async def test_no_metadata_found(self, agui_agent):
        agui_agent._session_lookup_cache.clear()
        agui_agent._session_manager._user_sessions = {}

        # Should not raise
        await agui_agent._remove_pending_tool_call("unknown-sess", "tc-1")

    async def test_tool_call_not_in_list(self, agui_agent):
        agui_agent._session_lookup_cache["sess-1"] = {"app_name": "app", "user_id": "user"}
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=["tc-other"])
        agui_agent._session_manager.set_state_value = AsyncMock()

        await agui_agent._remove_pending_tool_call("sess-1", "tc-1")

        agui_agent._session_manager.set_state_value.assert_not_awaited()

    async def test_handles_exception(self, agui_agent):
        agui_agent._session_lookup_cache["sess-1"] = {"app_name": "app", "user_id": "user"}
        agui_agent._session_manager.get_state_value = AsyncMock(side_effect=RuntimeError("fail"))

        await agui_agent._remove_pending_tool_call("sess-1", "tc-1")


# ---------------------------------------------------------------------------
# TestHasPendingToolCalls
# ---------------------------------------------------------------------------


class TestHasPendingToolCalls:
    async def test_returns_true_when_pending(self, agui_agent):
        agui_agent._session_lookup_cache["sess-1"] = {"app_name": "app", "user_id": "user"}
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=["tc-1"])

        result = await agui_agent._has_pending_tool_calls("sess-1")
        assert result is True

    async def test_returns_false_when_empty(self, agui_agent):
        agui_agent._session_lookup_cache["sess-1"] = {"app_name": "app", "user_id": "user"}
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=[])

        result = await agui_agent._has_pending_tool_calls("sess-1")
        assert result is False

    async def test_returns_false_when_no_metadata(self, agui_agent):
        agui_agent._session_lookup_cache.clear()
        agui_agent._session_manager._user_sessions = {}

        result = await agui_agent._has_pending_tool_calls("unknown")
        assert result is False

    async def test_returns_false_on_exception(self, agui_agent):
        agui_agent._session_lookup_cache["sess-1"] = {"app_name": "app", "user_id": "user"}
        agui_agent._session_manager.get_state_value = AsyncMock(side_effect=RuntimeError("boom"))

        result = await agui_agent._has_pending_tool_calls("sess-1")
        assert result is False


# ---------------------------------------------------------------------------
# TestRun
# ---------------------------------------------------------------------------


class TestRun:
    async def test_delegates_to_tool_result_submission(self, agui_agent):
        tool_msg = _make_tool_message()
        inp = _make_input(messages=[_make_user_message(), tool_msg])

        from ag_ui.core import RunErrorEvent
        agui_agent._handle_tool_result_submission = AsyncMock()

        async def fake_handler(*args, **kwargs):
            yield RunErrorEvent(type="RUN_ERROR", message="test", code="TEST")

        agui_agent._handle_tool_result_submission = fake_handler

        events = []
        async for event in agui_agent.run(inp):
            events.append(event)

        assert len(events) == 1

    async def test_delegates_to_start_new_execution(self, agui_agent):
        inp = _make_input(messages=[_make_user_message()])

        from ag_ui.core import RunStartedEvent, RunFinishedEvent

        async def fake_execution(*args, **kwargs):
            yield RunStartedEvent(type="RUN_STARTED", thread_id="t", run_id="r")
            yield RunFinishedEvent(type="RUN_FINISHED", thread_id="t", run_id="r")

        agui_agent._start_new_execution = fake_execution

        events = []
        async for event in agui_agent.run(inp):
            events.append(event)

        assert len(events) == 2


# ---------------------------------------------------------------------------
# TestHandleToolResultSubmission
# ---------------------------------------------------------------------------


class TestHandleToolResultSubmission:
    async def test_no_tool_results_yields_error(self, agui_agent):
        inp = _make_input(messages=[_make_user_message()])

        events = []
        async for event in agui_agent._handle_tool_result_submission(inp):
            events.append(event)

        assert len(events) == 1
        from ag_ui.core import RunErrorEvent
        assert isinstance(events[0], RunErrorEvent)
        assert events[0].code == "NO_TOOL_RESULTS"

    async def test_with_tool_results_delegates_to_new_execution(self, agui_agent):
        tc = _make_tool_call(tc_id="tc-1", name="search")
        assistant_msg = _make_assistant_message(tool_calls=[tc])
        tool_msg = _make_tool_message(content='{"ok": true}', tool_call_id="tc-1")
        inp = _make_input(messages=[assistant_msg, tool_msg])

        agui_agent._session_lookup_cache["thread-1"] = {"app_name": "test_app", "user_id": "test_user"}
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=["tc-1"])
        agui_agent._session_manager.set_state_value = AsyncMock(return_value=True)

        from ag_ui.core import RunStartedEvent, RunFinishedEvent

        async def fake_execution(*args, **kwargs):
            yield RunStartedEvent(type="RUN_STARTED", thread_id="t", run_id="r")
            yield RunFinishedEvent(type="RUN_FINISHED", thread_id="t", run_id="r")

        agui_agent._start_new_execution = fake_execution

        events = []
        async for event in agui_agent._handle_tool_result_submission(inp):
            events.append(event)

        assert len(events) == 2

    async def test_exception_yields_error_event(self, agui_agent):
        tc = _make_tool_call(tc_id="tc-1", name="search")
        assistant_msg = _make_assistant_message(tool_calls=[tc])
        tool_msg = _make_tool_message(content='{"ok": true}', tool_call_id="tc-1")
        inp = _make_input(messages=[assistant_msg, tool_msg])

        agui_agent._session_lookup_cache["thread-1"] = {"app_name": "test_app", "user_id": "test_user"}
        agui_agent._session_manager.get_state_value = AsyncMock(side_effect=RuntimeError("fail"))

        events = []
        async for event in agui_agent._handle_tool_result_submission(inp):
            events.append(event)

        from ag_ui.core import RunErrorEvent
        assert any(isinstance(e, RunErrorEvent) for e in events)


# ---------------------------------------------------------------------------
# TestStreamEvents
# ---------------------------------------------------------------------------


class TestStreamEvents:
    async def test_streams_events_until_none(self, agui_agent):
        from ag_ui.core import TextMessageStartEvent
        queue = asyncio.Queue()
        event1 = TextMessageStartEvent(type="TEXT_MESSAGE_START", message_id="m1", role="assistant")
        await queue.put(event1)
        await queue.put(None)

        task = asyncio.create_task(asyncio.sleep(10))
        exec_state = ExecutionState(task=task, thread_id="t1", event_queue=queue)

        events = []
        async for event in agui_agent._stream_events(exec_state):
            events.append(event)

        assert len(events) == 1
        assert events[0] is event1
        assert exec_state.is_complete is True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_breaks_on_stale_execution(self, agui_agent):
        agui_agent._execution_timeout = 0  # immediate timeout

        queue = asyncio.Queue()
        task = asyncio.create_task(asyncio.sleep(10))
        exec_state = ExecutionState(task=task, thread_id="t1", event_queue=queue)
        exec_state.start_time = 0  # very old

        events = []
        async for event in agui_agent._stream_events(exec_state):
            events.append(event)

        from ag_ui.core import RunErrorEvent
        assert any(isinstance(e, RunErrorEvent) and e.code == "EXECUTION_TIMEOUT" for e in events)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_breaks_when_task_done_and_queue_empty(self, agui_agent):
        queue = asyncio.Queue()

        async def quick_task():
            pass

        task = asyncio.create_task(quick_task())
        await task  # let it complete

        exec_state = ExecutionState(task=task, thread_id="t1", event_queue=queue)

        events = []
        async for event in agui_agent._stream_events(exec_state):
            events.append(event)

        assert exec_state.is_complete is True


# ---------------------------------------------------------------------------
# TestIsHitlTextScenario
# ---------------------------------------------------------------------------


class TestIsHitlTextScenario:
    async def test_detects_hitl_pattern(self, agui_agent):
        from trpc_agent_sdk import types
        from trpc_agent_sdk.events import Event

        func_call = types.FunctionCall(id="fc-1", name="ask_user", args={})
        second_last = Event(
            invocation_id="inv-1", author="agent",
            content=types.Content(role="model", parts=[types.Part(function_call=func_call)]),
        )
        func_resp = types.FunctionResponse(id="fc-1", name="ask_user", response={"text": "hello"})
        last = Event(
            invocation_id="inv-2", author="user",
            content=types.Content(role="function", parts=[types.Part(function_response=func_resp)]),
        )

        mock_session = Mock()
        mock_session.events = [second_last, last]
        agui_agent._session_manager._session_service.get_session = AsyncMock(return_value=mock_session)

        result = await agui_agent._is_hitl_text_scenario("t1", "app", "user")
        assert result is not None
        assert result.id == "fc-1"
        assert result.name == "ask_user"

    async def test_returns_none_when_no_session(self, agui_agent):
        agui_agent._session_manager._session_service.get_session = AsyncMock(return_value=None)
        result = await agui_agent._is_hitl_text_scenario("t1", "app", "user")
        assert result is None

    async def test_returns_none_when_not_enough_events(self, agui_agent):
        mock_session = Mock()
        mock_session.events = [Mock()]
        agui_agent._session_manager._session_service.get_session = AsyncMock(return_value=mock_session)

        result = await agui_agent._is_hitl_text_scenario("t1", "app", "user")
        assert result is None

    async def test_returns_none_when_ids_dont_match(self, agui_agent):
        from trpc_agent_sdk import types
        from trpc_agent_sdk.events import Event

        func_call = types.FunctionCall(id="fc-1", name="ask_user", args={})
        second_last = Event(
            invocation_id="inv-1", author="agent",
            content=types.Content(role="model", parts=[types.Part(function_call=func_call)]),
        )
        func_resp = types.FunctionResponse(id="fc-DIFFERENT", name="ask_user", response={"text": "hello"})
        last = Event(
            invocation_id="inv-2", author="user",
            content=types.Content(role="function", parts=[types.Part(function_response=func_resp)]),
        )

        mock_session = Mock()
        mock_session.events = [second_last, last]
        agui_agent._session_manager._session_service.get_session = AsyncMock(return_value=mock_session)

        result = await agui_agent._is_hitl_text_scenario("t1", "app", "user")
        assert result is None

    async def test_returns_none_on_exception(self, agui_agent):
        agui_agent._session_manager._session_service.get_session = AsyncMock(
            side_effect=RuntimeError("fail"))
        result = await agui_agent._is_hitl_text_scenario("t1", "app", "user")
        assert result is None

    async def test_returns_none_when_no_function_call_in_second_last(self, agui_agent):
        from trpc_agent_sdk import types
        from trpc_agent_sdk.events import Event

        second_last = Event(
            invocation_id="inv-1", author="agent",
            content=types.Content(role="model", parts=[types.Part(text="hello")]),
        )
        func_resp = types.FunctionResponse(id="fc-1", name="ask_user", response={"text": "hi"})
        last = Event(
            invocation_id="inv-2", author="user",
            content=types.Content(role="function", parts=[types.Part(function_response=func_resp)]),
        )

        mock_session = Mock()
        mock_session.events = [second_last, last]
        agui_agent._session_manager._session_service.get_session = AsyncMock(return_value=mock_session)

        result = await agui_agent._is_hitl_text_scenario("t1", "app", "user")
        assert result is None


# ---------------------------------------------------------------------------
# TestStartNewExecution
# ---------------------------------------------------------------------------


class TestStartNewExecution:
    async def test_emits_run_started_and_run_finished(self, agui_agent):
        from ag_ui.core import RunStartedEvent, RunFinishedEvent

        async def fake_bg_execution(input, http_request=None):
            queue = asyncio.Queue()
            await queue.put(None)

            async def noop():
                pass

            task = asyncio.create_task(noop())
            return ExecutionState(task=task, thread_id=input.thread_id, event_queue=queue)

        agui_agent._start_background_execution = fake_bg_execution
        agui_agent._session_lookup_cache["thread-1"] = {"app_name": "test_app", "user_id": "test_user"}
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=[])

        inp = _make_input(messages=[_make_user_message()])

        events = []
        async for event in agui_agent._start_new_execution(inp):
            events.append(event)

        assert isinstance(events[0], RunStartedEvent)
        assert isinstance(events[-1], RunFinishedEvent)

    async def test_max_concurrent_executions_error(self, agui_agent):
        agui_agent._max_concurrent = 0

        inp = _make_input(messages=[_make_user_message()])

        events = []
        async for event in agui_agent._start_new_execution(inp):
            events.append(event)

        from ag_ui.core import RunErrorEvent
        assert any(isinstance(e, RunErrorEvent) for e in events)

    async def test_cleans_up_execution_on_completion(self, agui_agent):
        async def fake_bg_execution(input, http_request=None):
            queue = asyncio.Queue()
            await queue.put(None)

            async def noop():
                pass

            task = asyncio.create_task(noop())
            return ExecutionState(task=task, thread_id=input.thread_id, event_queue=queue)

        agui_agent._start_background_execution = fake_bg_execution
        agui_agent._session_lookup_cache["thread-1"] = {"app_name": "test_app", "user_id": "test_user"}
        agui_agent._session_manager.get_state_value = AsyncMock(return_value=[])

        inp = _make_input(messages=[_make_user_message()])

        async for _ in agui_agent._start_new_execution(inp):
            pass

        assert "thread-1" not in agui_agent._active_executions


# ---------------------------------------------------------------------------
# TestStartBackgroundExecution
# ---------------------------------------------------------------------------


class TestStartBackgroundExecution:
    async def test_returns_execution_state(self, agui_agent):
        from ag_ui.core import SystemMessage as AGUISystemMessage

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)
        agui_agent._session_manager.get_session_state = AsyncMock(return_value={})

        inp = _make_input(messages=[_make_user_message("hello")])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            mock_runner = AsyncMock()

            async def empty_run(*args, **kwargs):
                return
                yield  # make it async gen

            mock_runner.run_async = empty_run
            MockRunner.return_value = mock_runner
            with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts",
                        return_value=[_make_text_part("hello")]):
                exec_state = await agui_agent._start_background_execution(inp)

        assert isinstance(exec_state, ExecutionState)
        assert exec_state.thread_id == "thread-1"

        # Clean up
        exec_state.task.cancel()
        try:
            await exec_state.task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_creates_toolset_when_tools_provided(self, agui_agent, mock_agent):
        from ag_ui.core import Tool as AGUIToolDef

        agui_tool = Mock(spec=AGUIToolDef)
        agui_tool.name = "frontend_tool"
        agui_tool.description = "A tool"
        agui_tool.parameters = {"type": "object", "properties": {"q": {"type": "string"}}}

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)
        agui_agent._session_manager.get_session_state = AsyncMock(return_value={})

        inp = _make_input(messages=[_make_user_message("hello")], tools=[agui_tool])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            mock_runner = AsyncMock()

            async def empty_run(*args, **kwargs):
                return
                yield

            mock_runner.run_async = empty_run
            MockRunner.return_value = mock_runner
            with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts",
                        return_value=[_make_text_part("hello")]):
                exec_state = await agui_agent._start_background_execution(inp)

        # model_copy should have been called with tools update
        mock_agent.model_copy.assert_called()

        exec_state.task.cancel()
        try:
            await exec_state.task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_appends_system_message_to_instructions(self, agui_agent, mock_agent):
        from ag_ui.core import SystemMessage as AGUISystemMessage

        sys_msg = Mock()
        sys_msg.role = "system"
        sys_msg.content = "You are a helpful assistant"
        sys_msg.__class__ = AGUISystemMessage

        mock_agent.instruction = "Base instruction"

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)
        agui_agent._session_manager.get_session_state = AsyncMock(return_value={})

        inp = _make_input(messages=[sys_msg, _make_user_message("hello")])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            mock_runner = AsyncMock()

            async def empty_run(*args, **kwargs):
                return
                yield

            mock_runner.run_async = empty_run
            MockRunner.return_value = mock_runner
            with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts",
                        return_value=[_make_text_part("hello")]):
                # Patch isinstance to detect our mock as SystemMessage
                with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.SystemMessage", type(sys_msg)):
                    exec_state = await agui_agent._start_background_execution(inp)

        exec_state.task.cancel()
        try:
            await exec_state.task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# TestRunTrpcInBackground
# ---------------------------------------------------------------------------


class TestRunTrpcInBackground:
    async def test_runs_agent_and_puts_events_in_queue(self, agui_agent, mock_agent):
        from trpc_agent_sdk.events import Event
        from trpc_agent_sdk import types

        queue = asyncio.Queue()
        trpc_event = Event(
            invocation_id="inv-1",
            author="agent",
            content=types.Content(role="model", parts=[types.Part(text="hello")]),
            partial=True,
            timestamp=1000.0,
        )

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)
        agui_agent._session_manager.get_session_state = AsyncMock(return_value={"key": "val"})

        inp = _make_input(messages=[_make_user_message("hello")])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            mock_runner = Mock()

            async def mock_run_async(**kwargs):
                yield trpc_event

            mock_runner.run_async = mock_run_async
            MockRunner.return_value = mock_runner
            with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts",
                        return_value=[_make_text_part("hello")]):
                await agui_agent._run_trpc_in_background(
                    input=inp, agent=mock_agent, user_id="test_user",
                    app_name="test_app", event_queue=queue,
                )

        # Should have put events + None sentinel
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert events[-1] is None  # sentinel
        assert len(events) >= 2  # at least one event + None

    async def test_handles_error_and_puts_error_event(self, agui_agent, mock_agent):
        queue = asyncio.Queue()

        agui_agent._session_manager.get_or_create_session = AsyncMock(
            side_effect=RuntimeError("session error"))
        agui_agent._session_manager.update_session_state = AsyncMock()

        inp = _make_input(messages=[_make_user_message("hello")])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            MockRunner.return_value = Mock()
            with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts",
                        return_value=[_make_text_part("hello")]):
                await agui_agent._run_trpc_in_background(
                    input=inp, agent=mock_agent, user_id="test_user",
                    app_name="test_app", event_queue=queue,
                )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        from ag_ui.core import RunErrorEvent
        assert any(isinstance(e, RunErrorEvent) for e in events)
        assert events[-1] is None

    async def test_no_message_yields_error(self, agui_agent, mock_agent):
        queue = asyncio.Queue()

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)

        inp = _make_input(messages=[])  # no messages

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            MockRunner.return_value = Mock()
            await agui_agent._run_trpc_in_background(
                input=inp, agent=mock_agent, user_id="test_user",
                app_name="test_app", event_queue=queue,
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        from ag_ui.core import RunErrorEvent
        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) >= 1

    async def test_handles_tool_result_submission_in_background(self, agui_agent, mock_agent):
        from trpc_agent_sdk.events import Event
        from trpc_agent_sdk import types

        queue = asyncio.Queue()
        trpc_event = Event(
            invocation_id="inv-1", author="agent",
            content=types.Content(role="model", parts=[types.Part(text="done")]),
            partial=False, timestamp=1000.0,
        )

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)
        agui_agent._session_manager.get_session_state = AsyncMock(return_value={})

        tc = _make_tool_call(tc_id="tc-1", name="search")
        assistant_msg = _make_assistant_message(tool_calls=[tc])
        tool_msg = _make_tool_message(content='{"ok": true}', tool_call_id="tc-1")
        inp = _make_input(messages=[assistant_msg, tool_msg])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            mock_runner = Mock()

            async def mock_run_async(**kwargs):
                yield trpc_event

            mock_runner.run_async = mock_run_async
            MockRunner.return_value = mock_runner
            await agui_agent._run_trpc_in_background(
                input=inp, agent=mock_agent, user_id="test_user",
                app_name="test_app", event_queue=queue,
            )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert events[-1] is None

    async def test_handles_lro_events(self, agui_agent, mock_agent):
        from trpc_agent_sdk.events import LongRunningEvent
        from trpc_agent_sdk import types

        queue = asyncio.Queue()
        func_call = types.FunctionCall(id="lro-1", name="long_tool", args={"q": "test"})
        func_resp = types.FunctionResponse(id="lro-1", name="long_tool", response={"result": "ok"})
        lro_event = LongRunningEvent(function_call=func_call, function_response=func_resp, timestamp=1000.0)

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)
        agui_agent._session_manager.get_session_state = AsyncMock(return_value={})

        inp = _make_input(messages=[_make_user_message("hello")])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            mock_runner = Mock()

            async def mock_run_async(**kwargs):
                yield lro_event

            mock_runner.run_async = mock_run_async
            MockRunner.return_value = mock_runner
            with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts",
                        return_value=[_make_text_part("hello")]):
                await agui_agent._run_trpc_in_background(
                    input=inp, agent=mock_agent, user_id="test_user",
                    app_name="test_app", event_queue=queue,
                )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert events[-1] is None
        # LRO events should have been translated to tool call events
        assert len(events) >= 2

    async def test_hitl_text_scenario_converts_to_function_response(self, agui_agent, mock_agent):
        from trpc_agent_sdk.events import Event
        from trpc_agent_sdk import types

        queue = asyncio.Queue()
        trpc_event = Event(
            invocation_id="inv-1", author="agent",
            content=types.Content(role="model", parts=[types.Part(text="result")]),
            partial=False, timestamp=1000.0,
        )

        func_call_obj = types.FunctionCall(id="fc-1", name="ask_user", args={})

        agui_agent._session_manager.get_or_create_session = AsyncMock(return_value=Mock())
        agui_agent._session_manager.update_session_state = AsyncMock(return_value=True)
        agui_agent._session_manager.get_session_state = AsyncMock(return_value={})

        # Create HITL scenario: _is_hitl_text_scenario returns a function_call
        agui_agent._is_hitl_text_scenario = AsyncMock(return_value=func_call_obj)

        inp = _make_input(messages=[_make_user_message("my answer")])

        with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.Runner") as MockRunner:
            mock_runner = Mock()

            async def mock_run_async(**kwargs):
                yield trpc_event

            mock_runner.run_async = mock_run_async
            MockRunner.return_value = mock_runner
            with patch("trpc_agent_sdk.server.ag_ui._core._agui_agent.convert_message_content_to_parts",
                        return_value=[_make_text_part("my answer")]):
                await agui_agent._run_trpc_in_background(
                    input=inp, agent=mock_agent, user_id="test_user",
                    app_name="test_app", event_queue=queue,
                )

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert events[-1] is None
