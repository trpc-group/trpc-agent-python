# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Real BaseTool filter-chain allow/deny/review and failure isolation tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.safety import OpenTelemetrySafetySink
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ToolArgumentExtractor
from trpc_agent_sdk.safety import ToolSafetyFilter
from trpc_agent_sdk.safety._monitor import MonitorSink
from trpc_agent_sdk.tools import BaseTool


class SpyTool(BaseTool):

    def __init__(self, result: Any, filters=None):
        super().__init__(name="script_tool", description="test", filters=filters)
        self.calls = 0
        self.result = result

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        del tool_context, args
        self.calls += 1
        return self.result


class OrderFilter(BaseFilter):

    def __init__(self, order):
        super().__init__()
        self.order = order

    async def _before(self, ctx, req, rsp):
        del ctx, req, rsp
        self.order.append("before")

    async def _after(self, ctx, req, rsp):
        del ctx, req, rsp
        self.order.append("after")


class FailingSink(MonitorSink):

    def emit(self, event):
        del event
        raise RuntimeError("observer failed")


@pytest.fixture()
def tool_context():
    context = MagicMock(spec=InvocationContext)
    context.agent_context = AgentContext()
    context.agent = MagicMock()
    context.agent.before_tool_callback = None
    context.agent.after_tool_callback = None
    context.invocation_id = "inv-safe"
    context.session_id = "session-safe"
    context.function_call_id = "call-123"
    return context


def _filter(scanner: SafetyScanner) -> ToolSafetyFilter:
    return ToolSafetyFilter(scanner, ToolArgumentExtractor(script_field="script", language_field="language"))


@pytest.mark.parametrize("value", [{"ok": True}, "text", 7, None])
async def test_allow_calls_real_tool_once_and_preserves_value_and_type(scanner, tool_context, value):
    tool = SpyTool(value, filters=[_filter(scanner)])
    result = await tool.run_async(tool_context=tool_context, args={"script": "print('ok')", "language": "python"})
    assert tool.calls == 1
    assert result is value


async def test_deny_calls_real_tool_zero_and_returns_structured_envelope(scanner, tool_context):
    tool = SpyTool("must not run", filters=[_filter(scanner)])
    result = await tool.run_async(
        tool_context=tool_context,
        args={
            "script": "import os\nos.remove('/etc/hosts')",
            "language": "python"
        },
    )
    assert tool.calls == 0
    assert result["safety"]["decision"] == "deny"
    assert result["safety"]["execution_blocked"] is True
    assert tool_context.function_call_id == "call-123"


async def test_review_calls_real_tool_zero(scanner, tool_context):
    tool = SpyTool("must not run", filters=[_filter(scanner)])
    result = await tool.run_async(
        tool_context=tool_context,
        args={
            "script": "import requests\nrequests.get(target)",
            "language": "python"
        },
    )
    assert tool.calls == 0
    assert result["safety"]["decision"] == "needs_human_review"


async def test_other_filter_order_is_preserved_on_allow(scanner, tool_context):
    order = []
    tool = SpyTool("ok", filters=[OrderFilter(order), _filter(scanner)])
    result = await tool.run_async(tool_context=tool_context, args={"script": "print('ok')", "language": "python"})
    assert result == "ok"
    assert tool.calls == 1
    assert order == ["before", "after"]


async def test_async_callbacks_keep_existing_semantics(scanner, tool_context):
    calls = []

    async def before(context, tool, args, response):
        del context, tool, args, response
        calls.append("before")

    async def after(context, tool, args, response):
        del context, tool, args, response
        calls.append("after")

    tool_context.agent.before_tool_callback = before
    tool_context.agent.after_tool_callback = after
    tool = SpyTool("ok", filters=[_filter(scanner)])
    from trpc_agent_sdk.context import reset_invocation_ctx
    from trpc_agent_sdk.context import set_invocation_ctx

    token = set_invocation_ctx(tool_context)
    try:
        result = await tool.run_async(tool_context=tool_context, args={"script": "print('ok')", "language": "python"})
    finally:
        reset_invocation_ctx(token)
    assert result == "ok"
    assert calls == ["before", "after"]


async def test_observer_and_otel_failures_do_not_change_allow_or_deny(policy, tool_context):
    scanner = SafetyScanner(
        policy,
        monitor_sinks=(FailingSink(), ),
        telemetry_sink=OpenTelemetrySafetySink(),
        report_observers=(lambda report: (_ for _ in ()).throw(RuntimeError("observer")), ),
    )
    allow_tool = SpyTool("ok", filters=[_filter(scanner)])
    deny_tool = SpyTool("bad", filters=[_filter(scanner)])
    assert await allow_tool.run_async(
        tool_context=tool_context,
        args={
            "script": "print('ok')",
            "language": "python"
        },
    ) == "ok"
    result = await deny_tool.run_async(
        tool_context=tool_context,
        args={
            "script": "import os\nos.remove('/etc/hosts')",
            "language": "python"
        },
    )
    assert result["safety"]["decision"] == "deny"
    assert allow_tool.calls == 1
    assert deny_tool.calls == 0
