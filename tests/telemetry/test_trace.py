# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.telemetry._trace.

Covers:
- set_trpc_agent_span_name / get_trpc_agent_span_name
- _safe_json_serialize
- trace_runner (with/without message, last_event, state)
- trace_cancellation (with/without partial_text, last_event, branch, state)
- trace_agent (with/without session, user_content, state)
- trace_tool_call (with/without function_response, BaseModel response, state)
- trace_merged_tool_calls (with/without serializable event, state)
- trace_call_llm (with/without usage_metadata, instruction_metadata)
- _build_llm_request_for_trace (filtering inline_data parts)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.telemetry._trace import (
    _build_llm_request_for_trace,
    _safe_json_serialize,
    get_trpc_agent_span_name,
    set_trpc_agent_span_name,
    trace_agent,
    trace_call_llm,
    trace_cancellation,
    trace_merged_tool_calls,
    trace_runner,
    trace_tool_call,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_span():
    """Return a MagicMock that acts as an OpenTelemetry span."""
    span = MagicMock()
    span.set_attribute = MagicMock()
    span.set_status = MagicMock()
    return span


def _make_part(text=None, function_call=None, function_response=None, inline_data=None):
    part = MagicMock()
    part.text = text
    part.function_call = function_call
    part.function_response = function_response
    part.inline_data = inline_data
    return part


def _make_content(parts=None, role="user"):
    content = MagicMock()
    content.parts = parts or []
    content.role = role
    return content


def _make_invocation_context(agent_name="test_agent", session_id="sess-1",
                             user_id="user-1", user_content=None, branch=None,
                             invocation_id="inv-1", session=None):
    ctx = MagicMock()
    ctx.agent = MagicMock()
    ctx.agent.name = agent_name
    ctx.invocation_id = invocation_id
    ctx.branch = branch
    ctx.user_content = user_content
    if session is None:
        ctx.session = MagicMock()
        ctx.session.id = session_id
        ctx.session.user_id = user_id
    else:
        ctx.session = session
    return ctx


def _make_event(content=None, event_id="evt-1", error_message=None, partial=False):
    event = MagicMock()
    event.content = content
    event.id = event_id
    event.error_message = error_message
    event.partial = partial
    return event


def _make_function_response(resp_id="fc-1", response=None):
    fr = MagicMock()
    fr.id = resp_id
    fr.response = response if response is not None else {"result": "ok"}
    return fr


# ---------------------------------------------------------------------------
# Tests: set/get span name
# ---------------------------------------------------------------------------

class TestSpanName:
    def setup_method(self):
        set_trpc_agent_span_name("trpc.python.agent")

    def test_get_default_span_name(self):
        assert get_trpc_agent_span_name() == "trpc.python.agent"

    def test_set_and_get_span_name(self):
        set_trpc_agent_span_name("custom.span")
        assert get_trpc_agent_span_name() == "custom.span"

    def test_set_empty_span_name(self):
        set_trpc_agent_span_name("")
        assert get_trpc_agent_span_name() == ""

    def teardown_method(self):
        set_trpc_agent_span_name("trpc.python.agent")


# ---------------------------------------------------------------------------
# Tests: _safe_json_serialize
# ---------------------------------------------------------------------------

class TestSafeJsonSerialize:
    def test_serialize_dict(self):
        result = _safe_json_serialize({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_serialize_list(self):
        result = _safe_json_serialize([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_serialize_string(self):
        result = _safe_json_serialize("hello")
        assert json.loads(result) == "hello"

    def test_serialize_number(self):
        result = _safe_json_serialize(42)
        assert json.loads(result) == 42

    def test_serialize_none(self):
        result = _safe_json_serialize(None)
        assert result == "null"

    def test_serialize_non_serializable_field(self):
        result = _safe_json_serialize({"fn": lambda x: x})
        parsed = json.loads(result)
        assert parsed["fn"] == "<not serializable>"

    def test_serialize_unicode(self):
        result = _safe_json_serialize({"msg": "你好世界"})
        assert "你好世界" in result

    def test_serialize_nested(self):
        obj = {"a": {"b": [1, {"c": True}]}}
        result = _safe_json_serialize(obj)
        assert json.loads(result) == obj

    def test_serialize_bool(self):
        assert json.loads(_safe_json_serialize(True)) is True

    def test_serialize_empty_dict(self):
        assert json.loads(_safe_json_serialize({})) == {}


# ---------------------------------------------------------------------------
# Tests: trace_runner
# ---------------------------------------------------------------------------

class TestTraceRunner:
    def setup_method(self):
        set_trpc_agent_span_name("trpc.python.agent")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_basic_attributes(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_runner(
            app_name="myapp",
            user_id="u1",
            session_id="s1",
            invocation_context=ctx,
        )

        span.set_attribute.assert_any_call("gen_ai.system", "trpc.python.agent")
        span.set_attribute.assert_any_call("gen_ai.operation.name", "run_runner")
        span.set_attribute.assert_any_call("trpc.python.agent.runner.app_name", "myapp")
        span.set_attribute.assert_any_call("trpc.python.agent.runner.user_id", "u1")
        span.set_attribute.assert_any_call("trpc.python.agent.runner.session_id", "s1")
        span.set_attribute.assert_any_call(
            "trpc.python.agent.runner.name",
            "[trpc-agent]: myapp/test_agent",
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_new_message(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        parts = [_make_part(text="hello"), _make_part(text="world")]
        content = _make_content(parts=parts)

        trace_runner(
            app_name="app",
            user_id="u",
            session_id="s",
            invocation_context=ctx,
            new_message=content,
        )

        span.set_attribute.assert_any_call("trpc.python.agent.runner.input", "hello\nworld")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_none_text_parts(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        parts = [_make_part(text=None), _make_part(text="ok")]
        content = _make_content(parts=parts)

        trace_runner(
            app_name="app",
            user_id="u",
            session_id="s",
            invocation_context=ctx,
            new_message=content,
        )

        span.set_attribute.assert_any_call("trpc.python.agent.runner.input", "\nok")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_last_event(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        event_parts = [_make_part(text="response")]
        event_content = _make_content(parts=event_parts)
        last_event = _make_event(content=event_content)

        trace_runner(
            app_name="app",
            user_id="u",
            session_id="s",
            invocation_context=ctx,
            last_event=last_event,
        )

        span.set_attribute.assert_any_call("trpc.python.agent.runner.output", "response")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_empty_input_output_without_message_and_event(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_runner("app", "u", "s", ctx)

        span.set_attribute.assert_any_call("trpc.python.agent.runner.input", "")
        span.set_attribute.assert_any_call("trpc.python.agent.runner.output", "")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_state_begin_and_end(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_runner("app", "u", "s", ctx, state_begin={"k": 1}, state_end={"k": 2})

        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.begin", _safe_json_serialize({"k": 1})
        )
        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.end", _safe_json_serialize({"k": 2})
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_state_none_not_set(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_runner("app", "u", "s", ctx)

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "trpc.python.agent.state.begin" not in attr_keys
        assert "trpc.python.agent.state.end" not in attr_keys

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_content_no_parts(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        content = _make_content(parts=[])
        trace_runner("app", "u", "s", ctx, new_message=content)

        span.set_attribute.assert_any_call("trpc.python.agent.runner.input", "")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_uses_custom_span_name(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        set_trpc_agent_span_name("my.custom")
        trace_runner("app", "u", "s", ctx)

        span.set_attribute.assert_any_call("gen_ai.system", "my.custom")
        span.set_attribute.assert_any_call("my.custom.runner.app_name", "app")

    def teardown_method(self):
        set_trpc_agent_span_name("trpc.python.agent")


# ---------------------------------------------------------------------------
# Tests: trace_cancellation
# ---------------------------------------------------------------------------

class TestTraceCancellation:
    def setup_method(self):
        set_trpc_agent_span_name("trpc.python.agent")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_basic_cancellation(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_cancellation(
            app_name="app",
            user_id="u",
            session_id="s",
            invocation_context=ctx,
            reason="user_cancelled",
        )

        from opentelemetry import trace as ot_trace
        span.set_status.assert_called_once_with(ot_trace.StatusCode.ERROR, "user_cancelled")
        span.set_attribute.assert_any_call("gen_ai.operation.name", "run_runner_cancelled")
        span.set_attribute.assert_any_call(
            "trpc.python.agent.cancellation.reason", "user_cancelled"
        )
        span.set_attribute.assert_any_call(
            "trpc.python.agent.cancellation.agent_name", "test_agent"
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_partial_text(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_cancellation("app", "u", "s", ctx, reason="timeout", partial_text="partial output")

        span.set_attribute.assert_any_call(
            "trpc.python.agent.runner.output", "[CANCELLED]\npartial output"
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_last_event_no_partial(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        event_parts = [_make_part(text="event text")]
        event_content = _make_content(parts=event_parts)
        last_event = _make_event(content=event_content)

        trace_cancellation("app", "u", "s", ctx, reason="err", last_event=last_event)

        span.set_attribute.assert_any_call(
            "trpc.python.agent.runner.output", "[CANCELLED]\nevent text"
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_partial_text_takes_priority_over_last_event(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        event_parts = [_make_part(text="event text")]
        event_content = _make_content(parts=event_parts)
        last_event = _make_event(content=event_content)

        trace_cancellation(
            "app", "u", "s", ctx,
            reason="err",
            partial_text="winner",
            last_event=last_event,
        )

        span.set_attribute.assert_any_call(
            "trpc.python.agent.runner.output", "[CANCELLED]\nwinner"
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_branch(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context(branch="agent_a.agent_b")

        trace_cancellation("app", "u", "s", ctx, reason="cancel")

        span.set_attribute.assert_any_call(
            "trpc.python.agent.cancellation.branch", "agent_a.agent_b"
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_no_branch(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context(branch=None)

        trace_cancellation("app", "u", "s", ctx, reason="cancel")

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "trpc.python.agent.cancellation.branch" not in attr_keys

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_state_begin_and_partial(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_cancellation(
            "app", "u", "s", ctx,
            reason="cancel",
            state_begin={"a": 1},
            state_partial={"a": 2},
        )

        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.begin", _safe_json_serialize({"a": 1})
        )
        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.partial", _safe_json_serialize({"a": 2})
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_state_none_not_set(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_cancellation("app", "u", "s", ctx, reason="cancel")

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "trpc.python.agent.state.begin" not in attr_keys
        assert "trpc.python.agent.state.partial" not in attr_keys

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_new_message(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        parts = [_make_part(text="user input")]
        content = _make_content(parts=parts)

        trace_cancellation("app", "u", "s", ctx, reason="cancel", new_message=content)

        span.set_attribute.assert_any_call("trpc.python.agent.runner.input", "user input")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_cancelled_output_no_partial_no_event(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_cancellation("app", "u", "s", ctx, reason="cancel")

        span.set_attribute.assert_any_call("trpc.python.agent.runner.output", "[CANCELLED]\n")

    def teardown_method(self):
        set_trpc_agent_span_name("trpc.python.agent")


# ---------------------------------------------------------------------------
# Tests: trace_agent
# ---------------------------------------------------------------------------

class TestTraceAgent:
    def setup_method(self):
        set_trpc_agent_span_name("trpc.python.agent")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_basic_agent_trace(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_agent(ctx, agent_action="called tool X")

        span.set_attribute.assert_any_call("gen_ai.system", "trpc.python.agent")
        span.set_attribute.assert_any_call("gen_ai.operation.name", "run_agent")
        span.set_attribute.assert_any_call("trpc.python.agent.agent.name", "test_agent")
        span.set_attribute.assert_any_call("trpc.python.agent.agent.output", "called tool X")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_session(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context(session_id="s-123", user_id="u-456")

        trace_agent(ctx)

        span.set_attribute.assert_any_call("trpc.python.agent.agent.session_id", "s-123")
        span.set_attribute.assert_any_call("trpc.python.agent.agent.user_id", "u-456")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_without_session(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context(session=None)
        ctx.session = None

        trace_agent(ctx)

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "trpc.python.agent.agent.session_id" not in attr_keys
        assert "trpc.python.agent.agent.user_id" not in attr_keys

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_user_content(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        parts = [_make_part(text="hello"), _make_part(text=" agent")]
        user_content = _make_content(parts=parts)
        ctx = _make_invocation_context(user_content=user_content)

        trace_agent(ctx)

        span.set_attribute.assert_any_call("trpc.python.agent.agent.input", "hello\n agent")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_without_user_content(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context(user_content=None)

        trace_agent(ctx)

        span.set_attribute.assert_any_call("trpc.python.agent.agent.input", "")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_state(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_agent(ctx, state_begin={"x": 1}, state_end={"x": 2})

        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.begin", _safe_json_serialize({"x": 1})
        )
        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.end", _safe_json_serialize({"x": 2})
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_default_empty_action(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        trace_agent(ctx)

        span.set_attribute.assert_any_call("trpc.python.agent.agent.output", "")

    def teardown_method(self):
        set_trpc_agent_span_name("trpc.python.agent")


# ---------------------------------------------------------------------------
# Tests: trace_tool_call
# ---------------------------------------------------------------------------

class TestTraceToolCall:
    def setup_method(self):
        set_trpc_agent_span_name("trpc.python.agent")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_basic_tool_call(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        tool = MagicMock()
        tool.name = "my_tool"
        tool.description = "A test tool"

        func_resp = _make_function_response(resp_id="fc-1", response={"output": "data"})
        part = _make_part(function_response=func_resp)
        content = _make_content(parts=[part])
        event = _make_event(content=content, event_id="e-1")

        trace_tool_call(tool=tool, args={"input": "val"}, function_response_event=event)

        span.set_attribute.assert_any_call("gen_ai.system", "trpc.python.agent")
        span.set_attribute.assert_any_call("gen_ai.operation.name", "execute_tool")
        span.set_attribute.assert_any_call("gen_ai.tool.name", "my_tool")
        span.set_attribute.assert_any_call("gen_ai.tool.description", "A test tool")
        span.set_attribute.assert_any_call("gen_ai.tool.call.id", "fc-1")
        span.set_attribute.assert_any_call("trpc.python.agent.event_id", "e-1")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_tool_call_no_function_response(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        tool = MagicMock()
        tool.name = "t"
        tool.description = "d"

        part = _make_part(function_response=None)
        content = _make_content(parts=[part])
        event = _make_event(content=content)

        trace_tool_call(tool=tool, args={}, function_response_event=event)

        span.set_attribute.assert_any_call("gen_ai.tool.call.id", "<not specified>")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_tool_response_not_dict(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        tool = MagicMock()
        tool.name = "t"
        tool.description = "d"

        func_resp = _make_function_response(response="plain string")
        part = _make_part(function_response=func_resp)
        content = _make_content(parts=[part])
        event = _make_event(content=content)

        trace_tool_call(tool=tool, args={}, function_response_event=event)

        # Should wrap in {"result": ...}
        tool_resp_calls = [
            c for c in span.set_attribute.call_args_list
            if c.args[0] == "trpc.python.agent.tool_response"
        ]
        assert len(tool_resp_calls) == 1
        parsed = json.loads(tool_resp_calls[0].args[1])
        assert parsed["result"] == "plain string"

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_tool_response_with_base_model_value(self, mock_get_span):
        from pydantic import BaseModel as PydanticBaseModel

        class MyModel(PydanticBaseModel):
            field: str = "value"

        span = _mock_span()
        mock_get_span.return_value = span

        tool = MagicMock()
        tool.name = "t"
        tool.description = "d"

        model_instance = MyModel()
        func_resp = _make_function_response(response={"model": model_instance})
        part = _make_part(function_response=func_resp)
        content = _make_content(parts=[part])
        event = _make_event(content=content)

        trace_tool_call(tool=tool, args={}, function_response_event=event)

        tool_resp_calls = [
            c for c in span.set_attribute.call_args_list
            if c.args[0] == "trpc.python.agent.tool_response"
        ]
        assert len(tool_resp_calls) == 1
        parsed = json.loads(tool_resp_calls[0].args[1])
        assert json.loads(parsed["model"]) == {"field": "value"}

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_sets_empty_llm_request_and_response(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        tool = MagicMock()
        tool.name = "t"
        tool.description = "d"

        func_resp = _make_function_response()
        part = _make_part(function_response=func_resp)
        content = _make_content(parts=[part])
        event = _make_event(content=content)

        trace_tool_call(tool=tool, args={}, function_response_event=event)

        span.set_attribute.assert_any_call("trpc.python.agent.llm_request", "{}")
        span.set_attribute.assert_any_call("trpc.python.agent.llm_response", "{}")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_state(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        tool = MagicMock()
        tool.name = "t"
        tool.description = "d"

        func_resp = _make_function_response()
        part = _make_part(function_response=func_resp)
        content = _make_content(parts=[part])
        event = _make_event(content=content)

        trace_tool_call(
            tool=tool, args={}, function_response_event=event,
            state_begin={"s": 0}, state_end={"s": 1},
        )

        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.begin", _safe_json_serialize({"s": 0})
        )
        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.end", _safe_json_serialize({"s": 1})
        )

    def teardown_method(self):
        set_trpc_agent_span_name("trpc.python.agent")


# ---------------------------------------------------------------------------
# Tests: trace_merged_tool_calls
# ---------------------------------------------------------------------------

class TestTraceMergedToolCalls:
    def setup_method(self):
        set_trpc_agent_span_name("trpc.python.agent")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_basic_merged(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        event = MagicMock()
        event.model_dumps_json = MagicMock(return_value='{"merged": true}')

        trace_merged_tool_calls(response_event_id="re-1", function_response_event=event)

        span.set_attribute.assert_any_call("gen_ai.system", "trpc.python.agent")
        span.set_attribute.assert_any_call("gen_ai.operation.name", "execute_tool")
        span.set_attribute.assert_any_call("gen_ai.tool.name", "(merged tools)")
        span.set_attribute.assert_any_call("gen_ai.tool.description", "(merged tools)")
        span.set_attribute.assert_any_call("gen_ai.tool.call.id", "re-1")
        span.set_attribute.assert_any_call("trpc.python.agent.event_id", "re-1")
        span.set_attribute.assert_any_call("trpc.python.agent.tool_call_args", "N/A")
        span.set_attribute.assert_any_call("trpc.python.agent.tool_response", '{"merged": true}')

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_non_serializable_event(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        event = MagicMock()
        event.model_dumps_json = MagicMock(side_effect=TypeError("cannot serialize"))

        trace_merged_tool_calls(response_event_id="re-1", function_response_event=event)

        span.set_attribute.assert_any_call(
            "trpc.python.agent.tool_response", "<not serializable>"
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_sets_empty_llm_request_and_response(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        event = MagicMock()
        event.model_dumps_json = MagicMock(return_value='{}')

        trace_merged_tool_calls(response_event_id="re-1", function_response_event=event)

        span.set_attribute.assert_any_call("trpc.python.agent.llm_request", "{}")
        span.set_attribute.assert_any_call("trpc.python.agent.llm_response", "{}")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_state(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        event = MagicMock()
        event.model_dumps_json = MagicMock(return_value='{}')

        trace_merged_tool_calls(
            response_event_id="re-1",
            function_response_event=event,
            state_begin={"a": 1},
            state_end={"a": 2},
        )

        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.begin", _safe_json_serialize({"a": 1})
        )
        span.set_attribute.assert_any_call(
            "trpc.python.agent.state.end", _safe_json_serialize({"a": 2})
        )

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_state_none_not_set(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span

        event = MagicMock()
        event.model_dumps_json = MagicMock(return_value='{}')

        trace_merged_tool_calls(response_event_id="re-1", function_response_event=event)

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "trpc.python.agent.state.begin" not in attr_keys
        assert "trpc.python.agent.state.end" not in attr_keys

    def teardown_method(self):
        set_trpc_agent_span_name("trpc.python.agent")


# ---------------------------------------------------------------------------
# Tests: trace_call_llm
# ---------------------------------------------------------------------------

class TestTraceCallLlm:
    def setup_method(self):
        set_trpc_agent_span_name("trpc.python.agent")

    def _make_llm_request(self, model="test-model", contents=None):
        req = MagicMock()
        req.model = model
        req.contents = contents or []
        req.config = MagicMock()
        req.config.model_dump = MagicMock(return_value={"temperature": 0.7})
        return req

    def _make_llm_response(self, content=None, usage=None, error_message=None):
        resp = MagicMock()
        resp.content = content
        resp.error_message = error_message
        resp.model_dump_json = MagicMock(return_value='{"content": "response"}')
        resp.usage_metadata = usage
        return resp

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_basic_llm_trace(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        req = self._make_llm_request()
        resp = self._make_llm_response()

        trace_call_llm(ctx, event_id="e-1", llm_request=req, llm_response=resp)

        span.set_attribute.assert_any_call("gen_ai.system", "trpc.python.agent")
        span.set_attribute.assert_any_call("gen_ai.operation.name", "call_llm")
        span.set_attribute.assert_any_call("gen_ai.request.model", "test-model")
        span.set_attribute.assert_any_call("trpc.python.agent.invocation_id", "inv-1")
        span.set_attribute.assert_any_call("trpc.python.agent.session_id", "sess-1")
        span.set_attribute.assert_any_call("trpc.python.agent.event_id", "e-1")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_usage_metadata(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        usage = MagicMock()
        usage.prompt_token_count = 100
        usage.total_token_count = 250

        req = self._make_llm_request()
        resp = self._make_llm_response(usage=usage)

        trace_call_llm(ctx, event_id="e-1", llm_request=req, llm_response=resp)

        span.set_attribute.assert_any_call("gen_ai.usage.input_tokens", 100)
        span.set_attribute.assert_any_call("gen_ai.usage.output_tokens", 150)

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_without_usage_metadata(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        req = self._make_llm_request()
        resp = self._make_llm_response(usage=None)

        trace_call_llm(ctx, event_id="e-1", llm_request=req, llm_response=resp)

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "gen_ai.usage.input_tokens" not in attr_keys
        assert "gen_ai.usage.output_tokens" not in attr_keys

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_usage_metadata_with_zero_prompt(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        usage = MagicMock()
        usage.prompt_token_count = 0
        usage.total_token_count = 0

        req = self._make_llm_request()
        resp = self._make_llm_response(usage=usage)

        trace_call_llm(ctx, event_id="e-1", llm_request=req, llm_response=resp)

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "gen_ai.usage.input_tokens" not in attr_keys

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_with_instruction_metadata(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        from trpc_agent_sdk.types import InstructionMetadata
        metadata = InstructionMetadata(
            name="my_instruction", version=3, labels=["prod", "v2"]
        )

        req = self._make_llm_request()
        resp = self._make_llm_response()

        trace_call_llm(
            ctx, event_id="e-1", llm_request=req, llm_response=resp,
            instruction_metadata=metadata,
        )

        span.set_attribute.assert_any_call("trpc.python.agent.instruction.name", "my_instruction")
        span.set_attribute.assert_any_call("trpc.python.agent.instruction.version", 3)
        span.set_attribute.assert_any_call("trpc.python.agent.instruction.labels", "prod,v2")

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_without_instruction_metadata(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        req = self._make_llm_request()
        resp = self._make_llm_response()

        trace_call_llm(ctx, event_id="e-1", llm_request=req, llm_response=resp)

        attr_keys = [call.args[0] for call in span.set_attribute.call_args_list]
        assert "trpc.python.agent.instruction.name" not in attr_keys

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_non_serializable_llm_response(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        req = self._make_llm_request()
        resp = self._make_llm_response()
        resp.model_dump_json = MagicMock(side_effect=Exception("serialize error"))

        trace_call_llm(ctx, event_id="e-1", llm_request=req, llm_response=resp)

        llm_resp_calls = [
            c for c in span.set_attribute.call_args_list
            if c.args[0] == "trpc.python.agent.llm_response"
        ]
        assert len(llm_resp_calls) == 1
        assert llm_resp_calls[0].args[1] == "<not serializable>"

    @patch("trpc_agent_sdk.telemetry._trace.trace.get_current_span")
    def test_instruction_metadata_empty_labels(self, mock_get_span):
        span = _mock_span()
        mock_get_span.return_value = span
        ctx = _make_invocation_context()

        from trpc_agent_sdk.types import InstructionMetadata
        metadata = InstructionMetadata(name="inst", version=1, labels=[])

        req = self._make_llm_request()
        resp = self._make_llm_response()

        trace_call_llm(
            ctx, event_id="e-1", llm_request=req, llm_response=resp,
            instruction_metadata=metadata,
        )

        span.set_attribute.assert_any_call("trpc.python.agent.instruction.labels", "")

    def teardown_method(self):
        set_trpc_agent_span_name("trpc.python.agent")


# ---------------------------------------------------------------------------
# Tests: _build_llm_request_for_trace
# ---------------------------------------------------------------------------

class TestBuildLlmRequestForTrace:
    def test_basic_build(self):
        content = MagicMock()
        content.role = "user"
        part_text = MagicMock()
        part_text.inline_data = None
        part_text.text = "hello"
        content.parts = [part_text]

        req = MagicMock()
        req.model = "gpt-4"
        req.config = MagicMock()
        req.config.model_dump = MagicMock(return_value={"temperature": 0.5})
        req.contents = [content]

        with patch("trpc_agent_sdk.telemetry._trace.Content") as MockContent:
            mock_content_instance = MagicMock()
            mock_content_instance.model_dump = MagicMock(
                return_value={"role": "user", "parts": [{"text": "hello"}]}
            )
            MockContent.return_value = mock_content_instance

            result = _build_llm_request_for_trace(req)

        assert result["model"] == "gpt-4"
        assert result["config"] == {"temperature": 0.5}
        assert len(result["contents"]) == 1

    def test_filters_inline_data_parts(self):
        content = MagicMock()
        content.role = "user"

        part_text = MagicMock()
        part_text.inline_data = None

        part_image = MagicMock()
        part_image.inline_data = b"image_bytes"

        content.parts = [part_text, part_image]

        req = MagicMock()
        req.model = "model"
        req.config = MagicMock()
        req.config.model_dump = MagicMock(return_value={})
        req.contents = [content]

        with patch("trpc_agent_sdk.telemetry._trace.Content") as MockContent:
            mock_content_instance = MagicMock()
            mock_content_instance.model_dump = MagicMock(
                return_value={"role": "user", "parts": [{"text": "t"}]}
            )
            MockContent.return_value = mock_content_instance

            result = _build_llm_request_for_trace(req)

        MockContent.assert_called_once()
        call_kwargs = MockContent.call_args
        passed_parts = call_kwargs.kwargs.get("parts") or call_kwargs[1].get("parts", [])
        if not passed_parts:
            passed_parts = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else []
        assert part_image not in (passed_parts if passed_parts else [])

    def test_multiple_contents(self):
        contents = []
        for role in ["user", "assistant"]:
            c = MagicMock()
            c.role = role
            p = MagicMock()
            p.inline_data = None
            c.parts = [p]
            contents.append(c)

        req = MagicMock()
        req.model = "model"
        req.config = MagicMock()
        req.config.model_dump = MagicMock(return_value={})
        req.contents = contents

        with patch("trpc_agent_sdk.telemetry._trace.Content") as MockContent:
            mock_inst = MagicMock()
            mock_inst.model_dump = MagicMock(return_value={"role": "x", "parts": []})
            MockContent.return_value = mock_inst

            result = _build_llm_request_for_trace(req)

        assert len(result["contents"]) == 2

    def test_empty_contents(self):
        req = MagicMock()
        req.model = "model"
        req.config = MagicMock()
        req.config.model_dump = MagicMock(return_value={})
        req.contents = []

        result = _build_llm_request_for_trace(req)

        assert result["contents"] == []
