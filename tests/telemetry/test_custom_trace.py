# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.telemetry._custom_trace.

Covers:
- _SyntheticTool: init, name, description, _run_async_impl raises
- CustomTraceReporter:
    - __init__ (defaults, custom params, text_content_filter)
    - _create_synthetic_llm_request (with/without user_content)
    - _create_synthetic_llm_response (with/without event)
    - _trace_function_call (single, multiple)
    - _trace_function_response (matched, unmatched, multiple)
    - _trace_llm_response (with/without instruction metadata)
    - _should_trace_text (empty, filter, no filter)
    - trace_event (partial skip, function_call, function_response, text, no text)
    - reset
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.telemetry._custom_trace import (
    CustomTraceReporter,
    _SyntheticTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_invocation_context(agent_name="test_agent", session_id="sess-1",
                             user_id="user-1", user_content=None,
                             invocation_id="inv-1", instruction=None):
    ctx = MagicMock()
    ctx.agent = MagicMock()
    ctx.agent.name = agent_name
    ctx.agent.instruction = instruction
    ctx.invocation_id = invocation_id
    ctx.user_content = user_content
    ctx.session = MagicMock()
    ctx.session.id = session_id
    ctx.session.user_id = user_id
    return ctx


def _make_function_call(name="tool_a", fc_id="fc-1", args=None):
    fc = MagicMock()
    fc.name = name
    fc.id = fc_id
    fc.args = args
    return fc


def _make_function_response(resp_id="fc-1", response=None):
    fr = MagicMock()
    fr.id = resp_id
    fr.response = response if response is not None else {"result": "ok"}
    return fr


def _make_event(
    partial=False,
    function_calls=None,
    function_responses=None,
    text="",
    event_id="evt-1",
    content=None,
    error_message=None,
):
    event = MagicMock()
    event.partial = partial
    event.id = event_id
    event.content = content
    event.error_message = error_message
    event.get_function_calls = MagicMock(return_value=function_calls or [])
    event.get_function_responses = MagicMock(return_value=function_responses or [])
    event.get_text = MagicMock(return_value=text)
    return event


# ---------------------------------------------------------------------------
# Tests: _SyntheticTool
# ---------------------------------------------------------------------------

class TestSyntheticTool:
    def test_init_with_name_and_description(self):
        tool = _SyntheticTool(name="my_tool", description="My tool desc")
        assert tool.name == "my_tool"
        assert tool.description == "My tool desc"

    def test_init_default_description(self):
        tool = _SyntheticTool(name="my_tool")
        assert tool.description == "Custom tool: my_tool"

    def test_init_empty_description_uses_default(self):
        tool = _SyntheticTool(name="t", description="")
        assert tool.description == "Custom tool: t"

    @pytest.mark.asyncio
    async def test_run_async_impl_raises(self):
        tool = _SyntheticTool(name="t")
        with pytest.raises(NotImplementedError, match="Synthetic tool should not be executed"):
            await tool._run_async_impl(tool_context=None, args={})


# ---------------------------------------------------------------------------
# Tests: CustomTraceReporter.__init__
# ---------------------------------------------------------------------------

class TestCustomTraceReporterInit:
    def test_default_init(self):
        reporter = CustomTraceReporter(agent_name="agent_a")
        assert reporter.agent_name == "agent_a"
        assert reporter.model_prefix == "custom"
        assert reporter.tool_description_prefix == "Custom tool"
        assert reporter.text_content_filter is None
        assert reporter.pending_function_calls == {}

    def test_custom_params(self):
        filt = lambda text: len(text) > 5
        reporter = CustomTraceReporter(
            agent_name="agent_b",
            model_prefix="a2a",
            tool_description_prefix="Remote tool",
            text_content_filter=filt,
        )
        assert reporter.model_prefix == "a2a"
        assert reporter.tool_description_prefix == "Remote tool"
        assert reporter.text_content_filter is filt


# ---------------------------------------------------------------------------
# Tests: _create_synthetic_llm_request
# ---------------------------------------------------------------------------

class TestCreateSyntheticLlmRequest:
    @patch("trpc_agent_sdk.telemetry._custom_trace.LlmRequest")
    @patch("trpc_agent_sdk.telemetry._custom_trace.GenerateContentConfig")
    def test_with_user_content(self, MockConfig, MockLlmRequest):
        reporter = CustomTraceReporter(agent_name="agent_x", model_prefix="pfx")
        user_content = MagicMock()
        ctx = _make_invocation_context(user_content=user_content)

        config_instance = MagicMock()
        MockConfig.return_value = config_instance

        reporter._create_synthetic_llm_request(ctx)

        MockLlmRequest.assert_called_once_with(
            model="pfx:agent_x",
            contents=[user_content],
            config=config_instance,
        )

    @patch("trpc_agent_sdk.telemetry._custom_trace.LlmRequest")
    @patch("trpc_agent_sdk.telemetry._custom_trace.GenerateContentConfig")
    def test_without_user_content(self, MockConfig, MockLlmRequest):
        reporter = CustomTraceReporter(agent_name="agent_x")
        ctx = _make_invocation_context(user_content=None)

        config_instance = MagicMock()
        MockConfig.return_value = config_instance

        reporter._create_synthetic_llm_request(ctx)

        MockLlmRequest.assert_called_once_with(
            model="custom:agent_x",
            contents=[],
            config=config_instance,
        )


# ---------------------------------------------------------------------------
# Tests: _create_synthetic_llm_response
# ---------------------------------------------------------------------------

class TestCreateSyntheticLlmResponse:
    @patch("trpc_agent_sdk.telemetry._custom_trace.LlmResponse")
    def test_with_event(self, MockLlmResponse):
        reporter = CustomTraceReporter(agent_name="a")
        event = _make_event(content="content_obj", error_message="err")

        reporter._create_synthetic_llm_response(event)

        MockLlmResponse.assert_called_once_with(
            content="content_obj",
            error_message="err",
        )

    @patch("trpc_agent_sdk.telemetry._custom_trace.LlmResponse")
    def test_with_none_event(self, MockLlmResponse):
        reporter = CustomTraceReporter(agent_name="a")
        event = None

        reporter._create_synthetic_llm_response(event)

        MockLlmResponse.assert_called_once_with(
            content=None,
            error_message=None,
        )


# ---------------------------------------------------------------------------
# Tests: _trace_function_call
# ---------------------------------------------------------------------------

class TestTraceFunctionCall:
    def test_single_function_call(self):
        reporter = CustomTraceReporter(agent_name="a")
        fc = _make_function_call(name="tool_1", fc_id="fc-1", args={"k": "v"})
        event = _make_event(function_calls=[fc])

        reporter._trace_function_call(event)

        assert "fc-1" in reporter.pending_function_calls
        assert reporter.pending_function_calls["fc-1"]["name"] == "tool_1"
        assert reporter.pending_function_calls["fc-1"]["args"] == {"k": "v"}

    def test_multiple_function_calls(self):
        reporter = CustomTraceReporter(agent_name="a")
        fc1 = _make_function_call(name="t1", fc_id="fc-1", args={"a": 1})
        fc2 = _make_function_call(name="t2", fc_id="fc-2", args=None)
        event = _make_event(function_calls=[fc1, fc2])

        reporter._trace_function_call(event)

        assert len(reporter.pending_function_calls) == 2
        assert reporter.pending_function_calls["fc-2"]["args"] == {}

    def test_args_none_becomes_empty_dict(self):
        reporter = CustomTraceReporter(agent_name="a")
        fc = _make_function_call(name="t", fc_id="fc-1", args=None)
        event = _make_event(function_calls=[fc])

        reporter._trace_function_call(event)

        assert reporter.pending_function_calls["fc-1"]["args"] == {}


# ---------------------------------------------------------------------------
# Tests: _trace_function_response
# ---------------------------------------------------------------------------

class TestTraceFunctionResponse:
    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_tool_call")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_matched_response(self, mock_tracer, mock_trace_tool_call):
        mock_tracer.start_as_current_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock()
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        reporter = CustomTraceReporter(
            agent_name="a",
            tool_description_prefix="Test tool",
        )
        reporter.pending_function_calls["fc-1"] = {
            "name": "tool_x",
            "args": {"input": "val"},
            "id": "fc-1",
        }

        fr = _make_function_response(resp_id="fc-1")
        event = _make_event(function_responses=[fr])

        reporter._trace_function_response(event)

        mock_tracer.start_as_current_span.assert_called_once_with("execute_tool tool_x")
        mock_trace_tool_call.assert_called_once()
        call_kwargs = mock_trace_tool_call.call_args.kwargs
        assert call_kwargs["args"] == {"input": "val"}
        assert call_kwargs["function_response_event"] is event
        assert "fc-1" not in reporter.pending_function_calls

    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_tool_call")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_unmatched_response_ignored(self, mock_tracer, mock_trace_tool_call):
        reporter = CustomTraceReporter(agent_name="a")
        fr = _make_function_response(resp_id="unknown-id")
        event = _make_event(function_responses=[fr])

        reporter._trace_function_response(event)

        mock_trace_tool_call.assert_not_called()

    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_tool_call")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_multiple_responses(self, mock_tracer, mock_trace_tool_call):
        mock_tracer.start_as_current_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock()
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        reporter = CustomTraceReporter(agent_name="a")
        reporter.pending_function_calls["fc-1"] = {
            "name": "t1", "args": {}, "id": "fc-1",
        }
        reporter.pending_function_calls["fc-2"] = {
            "name": "t2", "args": {}, "id": "fc-2",
        }

        fr1 = _make_function_response(resp_id="fc-1")
        fr2 = _make_function_response(resp_id="fc-2")
        event = _make_event(function_responses=[fr1, fr2])

        reporter._trace_function_response(event)

        assert mock_trace_tool_call.call_count == 2
        assert len(reporter.pending_function_calls) == 0


# ---------------------------------------------------------------------------
# Tests: _trace_llm_response
# ---------------------------------------------------------------------------

class TestTraceLlmResponse:
    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_call_llm")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_traces_llm_call(self, mock_tracer, mock_trace_call_llm):
        mock_tracer.start_as_current_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock()
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        reporter = CustomTraceReporter(agent_name="a", model_prefix="pfx")
        ctx = _make_invocation_context()
        event = _make_event(event_id="e-1")

        with patch.object(reporter, "_create_synthetic_llm_request") as mock_req, \
             patch.object(reporter, "_create_synthetic_llm_response") as mock_resp:
            mock_req.return_value = "fake_request"
            mock_resp.return_value = "fake_response"

            reporter._trace_llm_response(ctx, event)

        mock_tracer.start_as_current_span.assert_called_once_with("call_llm")
        mock_trace_call_llm.assert_called_once()
        call_kwargs = mock_trace_call_llm.call_args.kwargs
        assert call_kwargs["invocation_context"] is ctx
        assert call_kwargs["event_id"] == "e-1"
        assert call_kwargs["llm_request"] == "fake_request"
        assert call_kwargs["llm_response"] == "fake_response"

    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_call_llm")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_with_instruction_metadata(self, mock_tracer, mock_trace_call_llm):
        mock_tracer.start_as_current_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock()
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        reporter = CustomTraceReporter(agent_name="a")
        mock_metadata = MagicMock()
        mock_instruction = MagicMock()
        mock_instruction.metadata = mock_metadata
        ctx = _make_invocation_context(instruction=mock_instruction)
        event = _make_event()

        with patch.object(reporter, "_create_synthetic_llm_request") as mock_req, \
             patch.object(reporter, "_create_synthetic_llm_response") as mock_resp:
            mock_req.return_value = MagicMock()
            mock_resp.return_value = MagicMock()

            reporter._trace_llm_response(ctx, event)

        call_kwargs = mock_trace_call_llm.call_args.kwargs
        assert call_kwargs["instruction_metadata"] is mock_metadata

    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_call_llm")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_no_instruction(self, mock_tracer, mock_trace_call_llm):
        mock_tracer.start_as_current_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock()
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()
        ctx.agent.instruction = None
        event = _make_event()

        with patch.object(reporter, "_create_synthetic_llm_request") as mock_req, \
             patch.object(reporter, "_create_synthetic_llm_response") as mock_resp:
            mock_req.return_value = MagicMock()
            mock_resp.return_value = MagicMock()

            reporter._trace_llm_response(ctx, event)

        call_kwargs = mock_trace_call_llm.call_args.kwargs
        assert call_kwargs["instruction_metadata"] is None


# ---------------------------------------------------------------------------
# Tests: _should_trace_text
# ---------------------------------------------------------------------------

class TestShouldTraceText:
    def test_empty_text_returns_false(self):
        reporter = CustomTraceReporter(agent_name="a")
        assert reporter._should_trace_text("") is False

    def test_non_empty_text_no_filter_returns_true(self):
        reporter = CustomTraceReporter(agent_name="a")
        assert reporter._should_trace_text("hello") is True

    def test_filter_returns_true(self):
        reporter = CustomTraceReporter(
            agent_name="a",
            text_content_filter=lambda t: "ok" in t,
        )
        assert reporter._should_trace_text("this is ok") is True

    def test_filter_returns_false(self):
        reporter = CustomTraceReporter(
            agent_name="a",
            text_content_filter=lambda t: "ok" in t,
        )
        assert reporter._should_trace_text("not matching") is False

    def test_filter_with_none_text(self):
        reporter = CustomTraceReporter(
            agent_name="a",
            text_content_filter=lambda t: True,
        )
        assert reporter._should_trace_text("") is False


# ---------------------------------------------------------------------------
# Tests: trace_event
# ---------------------------------------------------------------------------

class TestTraceEvent:
    def test_skip_partial_event(self):
        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()
        event = _make_event(partial=True)

        with patch.object(reporter, "_trace_function_call") as m_fc, \
             patch.object(reporter, "_trace_function_response") as m_fr, \
             patch.object(reporter, "_trace_llm_response") as m_llm:
            reporter.trace_event(ctx, event)

        m_fc.assert_not_called()
        m_fr.assert_not_called()
        m_llm.assert_not_called()

    def test_function_call_event(self):
        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()
        fc = _make_function_call()
        event = _make_event(function_calls=[fc])

        with patch.object(reporter, "_trace_function_call") as m_fc, \
             patch.object(reporter, "_trace_function_response") as m_fr, \
             patch.object(reporter, "_trace_llm_response") as m_llm:
            reporter.trace_event(ctx, event)

        m_fc.assert_called_once_with(event)
        m_fr.assert_not_called()
        m_llm.assert_not_called()

    def test_function_response_event(self):
        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()
        fr = _make_function_response()
        event = _make_event(function_responses=[fr])

        with patch.object(reporter, "_trace_function_call") as m_fc, \
             patch.object(reporter, "_trace_function_response") as m_fr, \
             patch.object(reporter, "_trace_llm_response") as m_llm:
            reporter.trace_event(ctx, event)

        m_fc.assert_not_called()
        m_fr.assert_called_once_with(event)
        m_llm.assert_not_called()

    def test_text_event_traces_llm(self):
        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()
        event = _make_event(text="some response text")

        with patch.object(reporter, "_trace_function_call") as m_fc, \
             patch.object(reporter, "_trace_function_response") as m_fr, \
             patch.object(reporter, "_trace_llm_response") as m_llm:
            reporter.trace_event(ctx, event)

        m_fc.assert_not_called()
        m_fr.assert_not_called()
        m_llm.assert_called_once_with(ctx, event)

    def test_empty_text_event_skips_llm_trace(self):
        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()
        event = _make_event(text="")

        with patch.object(reporter, "_trace_function_call") as m_fc, \
             patch.object(reporter, "_trace_function_response") as m_fr, \
             patch.object(reporter, "_trace_llm_response") as m_llm:
            reporter.trace_event(ctx, event)

        m_llm.assert_not_called()

    def test_text_filtered_out_skips_llm_trace(self):
        reporter = CustomTraceReporter(
            agent_name="a",
            text_content_filter=lambda t: False,
        )
        ctx = _make_invocation_context()
        event = _make_event(text="some text")

        with patch.object(reporter, "_trace_llm_response") as m_llm:
            reporter.trace_event(ctx, event)

        m_llm.assert_not_called()

    def test_function_call_takes_priority_over_text(self):
        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()
        fc = _make_function_call()
        event = _make_event(function_calls=[fc], text="also has text")

        with patch.object(reporter, "_trace_function_call") as m_fc, \
             patch.object(reporter, "_trace_llm_response") as m_llm:
            reporter.trace_event(ctx, event)

        m_fc.assert_called_once()
        m_llm.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_pending(self):
        reporter = CustomTraceReporter(agent_name="a")
        reporter.pending_function_calls["fc-1"] = {"name": "t", "args": {}, "id": "fc-1"}
        reporter.pending_function_calls["fc-2"] = {"name": "t2", "args": {}, "id": "fc-2"}

        reporter.reset()

        assert reporter.pending_function_calls == {}

    def test_reset_idempotent(self):
        reporter = CustomTraceReporter(agent_name="a")
        reporter.reset()
        reporter.reset()
        assert reporter.pending_function_calls == {}


# ---------------------------------------------------------------------------
# Tests: Integration-like end-to-end flow
# ---------------------------------------------------------------------------

class TestEndToEndFlow:
    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_call_llm")
    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_tool_call")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_full_flow_fc_then_fr_then_text(
        self, mock_tracer, mock_trace_tool_call, mock_trace_call_llm
    ):
        mock_tracer.start_as_current_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock()
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        reporter = CustomTraceReporter(agent_name="my_agent", model_prefix="test")
        ctx = _make_invocation_context()

        # Step 1: function call event
        fc = _make_function_call(name="search", fc_id="fc-99", args={"q": "hello"})
        fc_event = _make_event(function_calls=[fc])
        reporter.trace_event(ctx, fc_event)

        assert "fc-99" in reporter.pending_function_calls
        mock_trace_tool_call.assert_not_called()

        # Step 2: function response event
        fr = _make_function_response(resp_id="fc-99", response={"answer": "world"})
        fr_event = _make_event(function_responses=[fr])
        reporter.trace_event(ctx, fr_event)

        mock_trace_tool_call.assert_called_once()
        assert "fc-99" not in reporter.pending_function_calls

        # Step 3: text response event (LLM trace)
        text_event = _make_event(text="Final answer: world")
        reporter.trace_event(ctx, text_event)

        mock_trace_call_llm.assert_called_once()

    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_call_llm")
    @patch("trpc_agent_sdk.telemetry._custom_trace.trace_tool_call")
    @patch("trpc_agent_sdk.telemetry._custom_trace.tracer")
    def test_reset_between_invocations(
        self, mock_tracer, mock_trace_tool_call, mock_trace_call_llm
    ):
        mock_tracer.start_as_current_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock()
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        reporter = CustomTraceReporter(agent_name="a")
        ctx = _make_invocation_context()

        fc = _make_function_call(name="t", fc_id="fc-1", args={})
        fc_event = _make_event(function_calls=[fc])
        reporter.trace_event(ctx, fc_event)

        assert len(reporter.pending_function_calls) == 1

        reporter.reset()
        assert len(reporter.pending_function_calls) == 0

        fr = _make_function_response(resp_id="fc-1")
        fr_event = _make_event(function_responses=[fr])
        reporter.trace_event(ctx, fr_event)

        mock_trace_tool_call.assert_not_called()
