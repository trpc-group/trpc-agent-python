# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for :class:`trpc_agent_sdk.telemetry.CustomMetricsReporter`.

Verifies the event-routing state machine:
  * partial events bump TTFT only
  * function-call events close an LLM segment and start tool timers
  * function-response events close tool timers and reopen an LLM segment
  * plain content events close an LLM segment

All ``report_*`` functions are patched to record the calls instead of emitting
to OTel, so the tests are hermetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import Dict
from typing import List
from unittest.mock import patch

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.telemetry import CustomMetricsReporter
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _ctx():
    return SimpleNamespace(
        app_name="demo",
        user_id="alice",
        agent_name="asst",
        agent=SimpleNamespace(model=None),
    )


def _text_event(text: str, *, partial: bool = False) -> Event:
    return Event(
        invocation_id="inv-1",
        author="asst",
        partial=partial,
        content=Content(parts=[Part.from_text(text=text)], role="model"),
    )


def _function_call_event(call_id: str, name: str) -> Event:
    return Event(
        invocation_id="inv-1",
        author="asst",
        content=Content(
            parts=[Part(function_call={
                "id": call_id,
                "name": name,
                "args": {
                    "x": 1
                },
            })],
            role="model",
        ),
    )


def _function_response_event(call_id: str, name: str, *, error: bool = False) -> Event:
    ev = Event(
        invocation_id="inv-1",
        author="tool",
        content=Content(
            parts=[Part(function_response={
                "id": call_id,
                "name": name,
                "response": {
                    "ok": not error
                },
            })],
            role="tool",
        ),
    )
    if error:
        ev.error_code = "500"
    return ev


class _Capture:
    """Helper to capture kwargs from patched ``report_*`` functions."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})


@pytest.fixture()
def patched_reporters():
    """Patch the two ``report_*`` functions imported into ``_custom_metrics``."""
    llm = _Capture()
    tool = _Capture()
    with patch("trpc_agent_sdk.telemetry._custom_metrics.report_call_llm",
               new=llm), patch("trpc_agent_sdk.telemetry._custom_metrics.report_execute_tool", new=tool):
        yield llm, tool


class TestCustomMetricsReporterRouting:

    def test_plain_content_event_emits_call_llm(self, patched_reporters):
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst", model_prefix="claude")

        reporter.report_event(_ctx(), _text_event("hello"))

        assert len(llm.calls) == 1
        assert len(tool.calls) == 0
        kw = llm.calls[0]["kwargs"]
        req = kw["llm_request"]
        assert req.model == "claude:asst"
        assert kw["is_stream"] is True  # default
        assert kw["duration_s"] >= 0.0
        assert kw["ttft_s"] >= 0.0

    def test_partial_event_does_not_emit(self, patched_reporters):
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst")

        reporter.report_event(_ctx(), _text_event("chunk", partial=True))
        reporter.report_event(_ctx(), _text_event("chunk 2", partial=True))

        assert llm.calls == []
        assert tool.calls == []
        # TTFT is latched as soon as any content event arrives, partial or not.
        assert reporter._llm_ttft is not None

    def test_function_call_closes_llm_and_opens_tool_timers(self, patched_reporters):
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst")

        reporter.report_event(_ctx(), _function_call_event("c1", "search"))

        assert len(llm.calls) == 1, "function-call event must emit the open LLM segment"
        assert len(tool.calls) == 0
        assert reporter._pending_tool_starts.keys() == {"c1"}
        assert reporter._pending_tool_starts["c1"][0] == "search"
        assert reporter._llm_segment_start is None

    def test_function_response_emits_execute_tool_and_reopens_segment(self, patched_reporters):
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst")

        reporter.report_event(_ctx(), _function_call_event("c1", "search"))
        reporter.report_event(_ctx(), _function_response_event("c1", "search"))

        assert len(tool.calls) == 1
        kw = tool.calls[0]["kwargs"]
        assert kw["duration_s"] >= 0.0
        assert kw["error_type"] is None
        assert tool.calls[0]["args"][1].name == "search"
        assert reporter._pending_tool_starts == {}
        assert reporter._llm_segment_start is not None

    def test_tool_error_type_propagates(self, patched_reporters):
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst")

        reporter.report_event(_ctx(), _function_call_event("c1", "search"))
        reporter.report_event(_ctx(), _function_response_event("c1", "search", error=True))

        assert tool.calls[0]["kwargs"]["error_type"] == "500"

    def test_unmatched_function_response_is_ignored(self, patched_reporters):
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst")

        # No matching function_call beforehand.
        reporter.report_event(_ctx(), _function_response_event("unknown", "search"))

        assert tool.calls == []
        # Segment was still reopened.
        assert reporter._llm_segment_start is not None

    def test_full_round_trip_chat_tool_chat(self, patched_reporters):
        """LLM call -> tool call -> tool result -> final LLM chunk."""
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst", model_prefix="a2a")

        reporter.report_event(_ctx(), _function_call_event("c1", "search"))
        reporter.report_event(_ctx(), _function_response_event("c1", "search"))
        reporter.report_event(_ctx(), _text_event("final answer"))

        assert len(llm.calls) == 2, "two LLM segments: before tool + after tool"
        assert len(tool.calls) == 1
        for call in llm.calls:
            assert call["kwargs"]["llm_request"].model == "a2a:asst"

    def test_extra_attributes_forwarded(self, patched_reporters):
        llm, tool = patched_reporters
        reporter = CustomMetricsReporter(
            agent_name="asst",
            extra_attributes={"gen_ai.system": "openai"},
        )
        reporter.report_event(_ctx(), _function_call_event("c1", "search"))
        reporter.report_event(_ctx(), _function_response_event("c1", "search"))

        assert llm.calls[0]["kwargs"]["extra_attributes"] == {"gen_ai.system": "openai"}
        assert tool.calls[0]["kwargs"]["extra_attributes"] == {"gen_ai.system": "openai"}


class TestCustomMetricsReporterReset:

    def test_reset_clears_pending_state(self, patched_reporters):
        _, _ = patched_reporters
        reporter = CustomMetricsReporter(agent_name="asst")
        reporter.report_event(_ctx(), _function_call_event("c1", "search"))
        assert reporter._pending_tool_starts

        reporter.reset()

        assert reporter._pending_tool_starts == {}
        assert reporter._llm_segment_start is None
        assert reporter._llm_ttft is None
