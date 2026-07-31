# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fake ClientSession-only MCP adapter tests; no server is started."""

from __future__ import annotations

from unittest.mock import AsyncMock

from trpc_agent_sdk.safety import SafetyMCPAdapter
from trpc_agent_sdk.safety import SafetyScanRequest


def _extract(name, arguments):
    return SafetyScanRequest(
        script=arguments["script"],
        language=arguments.get("language", "python"),
        tool_name=name,
        source_type="mcp",
    )


async def test_mcp_allow_forwards_name_and_same_arguments(scanner):
    session = AsyncMock()
    response = object()
    session.call_tool.return_value = response
    adapter = SafetyMCPAdapter(session, scanner, {"run_script": _extract})
    arguments = {"script": "print('ok')", "language": "python"}
    result = await adapter.call_tool("run_script", arguments)
    assert result is response
    session.call_tool.assert_awaited_once_with("run_script", arguments=arguments)


async def test_mcp_deny_and_review_call_tool_zero(scanner):
    session = AsyncMock()
    adapter = SafetyMCPAdapter(session, scanner, {"run_script": _extract})
    denied = await adapter.call_tool("run_script", {"script": "import os\nos.remove('/etc/hosts')"})
    reviewed = await adapter.call_tool("run_script", {"script": "import requests\nrequests.get(target)"})
    assert session.call_tool.await_count == 0
    assert denied["safety"]["decision"] == "deny"
    assert reviewed["safety"]["decision"] == "needs_human_review"


async def test_unconfigured_business_strings_are_not_scanned(scanner):
    session = AsyncMock()
    session.call_tool.return_value = "ok"
    adapter = SafetyMCPAdapter(session, scanner)
    arguments = {"message": "please discuss rm -rf / but do not execute it"}
    assert await adapter.call_tool("chat", arguments) == "ok"
    session.call_tool.assert_awaited_once_with("chat", arguments=arguments)


async def test_unconfigured_none_arguments_are_forwarded_unchanged(scanner):
    session = AsyncMock()
    session.call_tool.return_value = "ok"
    adapter = SafetyMCPAdapter(session, scanner)
    assert await adapter.call_tool("ping", None) == "ok"
    session.call_tool.assert_awaited_once_with("ping", arguments=None)
