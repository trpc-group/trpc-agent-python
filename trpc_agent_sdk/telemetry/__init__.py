# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Telemetry module for TRPC Agent framework."""

from ._custom_trace import CustomTraceReporter
from ._trace import get_trpc_agent_span_name
from ._trace import set_trpc_agent_span_name
from ._trace import trace_agent
from ._trace import trace_call_llm
from ._trace import trace_cancellation
from ._trace import trace_merged_tool_calls
from ._trace import trace_runner
from ._trace import trace_tool_call
from ._trace import tracer

__all__ = [
    "CustomTraceReporter",
    "trace_agent",
    "trace_call_llm",
    "trace_cancellation",
    "trace_merged_tool_calls",
    "trace_runner",
    "trace_tool_call",
    "tracer",
    "get_trpc_agent_span_name",
    "set_trpc_agent_span_name",
]
