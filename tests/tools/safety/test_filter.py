"""Tests for the tool safety filter integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk._tool_safety_policy import ToolSafetyPolicy
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import ToolSafetyFilter


@pytest.fixture
def mock_tool_context():
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = MagicMock()
    ctx.agent = MagicMock()
    ctx.agent.before_tool_callback = None
    ctx.agent.after_tool_callback = None
    ctx.agent.parallel_tool_calls = False
    return ctx


async def test_filter_allows_safe_tool_request() -> None:
    safety_filter = ToolSafetyFilter()
    rsp = FilterResult()

    with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var", return_value=SimpleNamespace(name="safe_tool")):
        await safety_filter._before(MagicMock(), {"query": "hello"}, rsp)

    assert rsp.rsp is None
    assert rsp.error is None
    assert rsp.is_continue is True


async def test_filter_blocks_deny_decision_before_tool_runs(mock_tool_context) -> None:
    called = False

    def dangerous_tool(command: str):
        nonlocal called
        called = True
        return {"command": command}

    tool = FunctionTool(dangerous_tool, filters=[ToolSafetyFilter()])

    result = await tool.run_async(tool_context=mock_tool_context, args={"command": "rm -rf /tmp/demo"})

    assert called is False
    assert result["success"] is False
    assert result["error"].startswith("TOOL_SAFETY_BLOCKED:")
    assert result["safety"]["decision"] == "deny"
    assert result["safety"]["rule_id"] == "dangerous_delete"
    assert result["safety"]["tool_name"] == "dangerous_tool"
    assert result["safety_audit"]["action_type"] == "bash"


async def test_filter_blocks_needs_human_review_by_default() -> None:
    safety_filter = ToolSafetyFilter()
    rsp = FilterResult()

    with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var", return_value=SimpleNamespace(name="Bash")):
        await safety_filter._before(MagicMock(), {"command": "npm install left-pad"}, rsp)

    assert rsp.is_continue is False
    assert rsp.error is None
    assert rsp.rsp["safety"]["decision"] == "needs_human_review"
    assert rsp.rsp["safety"]["rule_id"] == "npm_install"


async def test_filter_policy_allows_allowlisted_network_request() -> None:
    safety_filter = ToolSafetyFilter(policy=ToolSafetyPolicy(allowed_domains=("api.example.com", )))
    rsp = FilterResult()

    with patch("trpc_agent_sdk.tools.safety._filter.get_tool_var", return_value=SimpleNamespace(name="web_fetch")):
        await safety_filter._before(MagicMock(), {"url": "https://api.example.com/v1/items"}, rsp)

    assert rsp.rsp is None
    assert rsp.error is None
    assert rsp.is_continue is True


async def test_filter_can_allow_human_review_decisions_when_configured(mock_tool_context) -> None:
    called = False

    def install_tool(command: str):
        nonlocal called
        called = True
        return {"accepted": command}

    tool = FunctionTool(install_tool, filters=[ToolSafetyFilter(block_decisions=("deny", ))])

    result = await tool.run_async(tool_context=mock_tool_context, args={"command": "npm install left-pad"})

    assert called is True
    assert result == {"accepted": "npm install left-pad"}
