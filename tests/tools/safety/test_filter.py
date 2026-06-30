# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Acceptance 7: the filter blocks high-risk tools BEFORE execution and audits.

The decisive assertion is that on a DENY the next handler (the call that runs the
tool body) is never invoked -- proven with a side-effect flag, not just the
return value.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools._base_tool import BaseTool
from trpc_agent_sdk.tools.safety.audit import AuditLogger
from trpc_agent_sdk.tools.safety.filter import ToolSafetyFilter


def _make_context() -> InvocationContext:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = MagicMock()
    ctx.agent = MagicMock()
    ctx.agent.before_tool_callback = None
    ctx.agent.after_tool_callback = None
    return ctx


class _DemoTool(BaseTool):
    """Records whether its body actually executed."""

    def __init__(self) -> None:
        super().__init__(name="Bash", description="demo", filters_name=["tool_safety_guard"])
        self.executed = False

    async def _run_async_impl(self, *, tool_context, args):
        self.executed = True
        return {"ok": True, "command": args.get("command")}


class TestFilterRunDirectly:

    @pytest.mark.asyncio
    async def test_deny_does_not_invoke_handle(self, tmp_path):
        guard = ToolSafetyFilter()
        guard._audit = AuditLogger(str(tmp_path / "audit.jsonl"))
        called = {"value": False}

        async def handle():
            called["value"] = True  # would mean the tool body ran
            return FilterResult(rsp={"executed": True})

        result = await guard.run(ctx=None, req={"command": "rm -rf /"}, handle=handle)

        assert called["value"] is False  # the tool body was NOT reached
        assert result.is_continue is False
        assert result.rsp["blocked"] is True
        assert result.rsp["safety"]["decision"] == "deny"

    @pytest.mark.asyncio
    async def test_deny_writes_blocked_audit_event(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        guard = ToolSafetyFilter()
        guard._audit = AuditLogger(str(audit_path))

        async def handle():
            return FilterResult(rsp={"executed": True})

        await guard.run(ctx=None, req={"command": "rm -rf /"}, handle=handle)

        records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert records, "an audit event must be written"
        assert records[-1]["blocked"] is True
        assert records[-1]["decision"] == "deny"

    @pytest.mark.asyncio
    async def test_allow_invokes_handle(self):
        guard = ToolSafetyFilter()
        called = {"value": False}

        async def handle():
            called["value"] = True
            return FilterResult(rsp={"executed": True})

        result = await guard.run(ctx=None, req={"command": "ls -la"}, handle=handle)

        assert called["value"] is True
        assert result.rsp == {"executed": True}


class TestFilterEndToEnd:

    @pytest.mark.asyncio
    async def test_dangerous_tool_call_is_blocked(self):
        tool = _DemoTool()
        result = await tool.run_async(tool_context=_make_context(), args={"command": "rm -rf /"})
        assert tool.executed is False
        assert result.get("blocked") is True

    @pytest.mark.asyncio
    async def test_safe_tool_call_executes(self):
        tool = _DemoTool()
        result = await tool.run_async(tool_context=_make_context(), args={"command": "echo hello"})
        assert tool.executed is True
        assert result["ok"] is True
