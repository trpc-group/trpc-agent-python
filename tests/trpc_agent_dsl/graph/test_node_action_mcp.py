# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Execution-path tests for MCPNodeAction."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph._event_writer import AsyncEventWriter
from trpc_agent_sdk.dsl.graph._event_writer import EventWriter
from trpc_agent_sdk.dsl.graph._node_action._mcp import MCPNodeAction


def _build_action(
    mcp_toolset: Any,
    selected_tool_name: str = "search",
    req_src_node: str = "prev-node",
    *,
    ctx: Any = None,
) -> MCPNodeAction:
    """Create MCPNodeAction with concrete event writer instances."""
    writer = EventWriter(
        writer=lambda payload: None,
        invocation_id="inv-1",
        author="mcp-node",
        branch="root.mcp-node",
    )
    async_writer = AsyncEventWriter(
        writer=lambda payload: None,
        invocation_id="inv-1",
        author="mcp-node",
        branch="root.mcp-node",
    )
    return MCPNodeAction(
        name="mcp-node",
        mcp_toolset=mcp_toolset,
        selected_tool_name=selected_tool_name,
        req_src_node=req_src_node,
        writer=writer,
        async_writer=async_writer,
        ctx=ctx,
    )


def _make_toolset(tools: list[Any], close: Any = None) -> SimpleNamespace:
    """Build a stub MCPToolset with configurable tools and close behavior."""
    return SimpleNamespace(
        get_tools=AsyncMock(return_value=tools),
        close=close or AsyncMock(),
    )


def _make_tool(name: str, response: Any) -> SimpleNamespace:
    """Build a stub BaseTool that returns a scripted response."""
    return SimpleNamespace(name=name, run_async=AsyncMock(return_value=response))


class TestMCPNodeActionInit:
    """Tests for MCPNodeAction constructor."""

    def test_strips_whitespace_from_tool_and_node_names(self):
        toolset = _make_toolset([])
        action = _build_action(toolset, selected_tool_name="  search  ", req_src_node="  prev  ")
        assert action.selected_tool_name == "search"
        assert action.req_src_node == "prev"


class TestTryParseJson:
    """Tests for MCPNodeAction._try_parse_json static method."""

    def test_returns_non_string_values_as_is(self):
        assert MCPNodeAction._try_parse_json(42) == 42
        assert MCPNodeAction._try_parse_json([1, 2]) == [1, 2]
        assert MCPNodeAction._try_parse_json(None) is None

    def test_parses_valid_json_string(self):
        assert MCPNodeAction._try_parse_json('{"key": "value"}') == {"key": "value"}
        assert MCPNodeAction._try_parse_json("[1, 2, 3]") == [1, 2, 3]
        assert MCPNodeAction._try_parse_json('"hello"') == "hello"

    def test_returns_original_for_invalid_json(self):
        assert MCPNodeAction._try_parse_json("not json") == "not json"
        assert MCPNodeAction._try_parse_json("{bad}") == "{bad}"

    def test_returns_empty_string_as_is(self):
        assert MCPNodeAction._try_parse_json("") == ""

    def test_parses_whitespace_padded_json(self):
        assert MCPNodeAction._try_parse_json('  {"a": 1}  ') == {"a": 1}

    def test_returns_whitespace_only_string_as_is(self):
        assert MCPNodeAction._try_parse_json("   ") == "   "


class TestResolveRequestArgs:
    """Tests for MCPNodeAction._resolve_request_args."""

    def test_extracts_args_from_node_responses(self):
        toolset = _make_toolset([])
        action = _build_action(toolset, req_src_node="prev-node")
        state = {"node_responses": {"prev-node": {"query": "test"}}}
        assert action._resolve_request_args(state) == {"query": "test"}

    def test_raises_when_node_responses_is_not_dict(self):
        toolset = _make_toolset([])
        action = _build_action(toolset, req_src_node="prev-node")
        state = {"node_responses": "invalid"}
        with pytest.raises(ValueError, match="to be a dict"):
            action._resolve_request_args(state)

    def test_raises_when_source_node_missing_from_responses(self):
        toolset = _make_toolset([])
        action = _build_action(toolset, req_src_node="missing-node")
        state = {"node_responses": {"other-node": {}}}
        with pytest.raises(ValueError, match="missing"):
            action._resolve_request_args(state)

    def test_raises_when_source_node_payload_is_not_dict(self):
        toolset = _make_toolset([])
        action = _build_action(toolset, req_src_node="prev-node")
        state = {"node_responses": {"prev-node": "not-a-dict"}}
        with pytest.raises(ValueError, match="to be a dict"):
            action._resolve_request_args(state)


class TestResolveSelectedTool:
    """Tests for MCPNodeAction._resolve_selected_tool."""

    async def test_returns_matching_tool(self):
        tool = _make_tool("search", "result")
        toolset = _make_toolset([_make_tool("other", "x"), tool])
        action = _build_action(toolset, selected_tool_name="search")
        ctx = SimpleNamespace()
        result = await action._resolve_selected_tool(ctx)
        assert result is tool

    async def test_raises_when_tool_not_found(self):
        toolset = _make_toolset([_make_tool("other", "x")])
        action = _build_action(toolset, selected_tool_name="missing")
        ctx = SimpleNamespace()
        with pytest.raises(ValueError, match="cannot find selected tool"):
            await action._resolve_selected_tool(ctx)

    async def test_raises_when_no_tools_available(self):
        toolset = _make_toolset([])
        action = _build_action(toolset, selected_tool_name="search")
        ctx = SimpleNamespace()
        with pytest.raises(ValueError, match="cannot find selected tool"):
            await action._resolve_selected_tool(ctx)


class TestMCPNodeActionExecute:
    """Tests for MCPNodeAction.execute end-to-end flow."""

    async def test_execute_success_with_json_response(self):
        """Execute should resolve tool, call it, parse JSON, and map state."""
        tool = _make_tool("search", '{"results": [1, 2]}')
        toolset = _make_toolset([tool])
        ctx = SimpleNamespace()
        action = _build_action(toolset, selected_tool_name="search", req_src_node="prev-node", ctx=ctx)

        state = {"node_responses": {"prev-node": {"query": "test"}}}
        result = await action.execute(state)

        assert result[STATE_KEY_LAST_RESPONSE] == '{"results": [1, 2]}'
        assert result[STATE_KEY_NODE_RESPONSES] == {"mcp-node": {"results": [1, 2]}}
        tool.run_async.assert_awaited_once_with(tool_context=ctx, args={"query": "test"})
        toolset.close.assert_awaited_once()

    async def test_execute_success_with_non_json_response(self):
        """Non-JSON string responses should be stored as-is in node_responses."""
        tool = _make_tool("greet", "Hello world")
        toolset = _make_toolset([tool])
        ctx = SimpleNamespace()
        action = _build_action(toolset, selected_tool_name="greet", req_src_node="prev-node", ctx=ctx)

        state = {"node_responses": {"prev-node": {"name": "user"}}}
        result = await action.execute(state)

        assert result[STATE_KEY_LAST_RESPONSE] == "Hello world"
        assert result[STATE_KEY_NODE_RESPONSES] == {"mcp-node": "Hello world"}

    async def test_execute_success_with_dict_response(self):
        """Dict responses bypass JSON parsing and are stored directly."""
        raw_dict = {"key": "value", "count": 5}
        tool = _make_tool("api", raw_dict)
        toolset = _make_toolset([tool])
        ctx = SimpleNamespace()
        action = _build_action(toolset, selected_tool_name="api", req_src_node="prev-node", ctx=ctx)

        state = {"node_responses": {"prev-node": {"param": "x"}}}
        result = await action.execute(state)

        assert result[STATE_KEY_LAST_RESPONSE] == raw_dict
        assert result[STATE_KEY_NODE_RESPONSES] == {"mcp-node": raw_dict}

    async def test_execute_closes_toolset_after_call(self):
        """Toolset.close() must be called even on success."""
        tool = _make_tool("search", "ok")
        toolset = _make_toolset([tool])
        ctx = SimpleNamespace()
        action = _build_action(toolset, selected_tool_name="search", req_src_node="prev-node", ctx=ctx)

        state = {"node_responses": {"prev-node": {}}}
        await action.execute(state)

        toolset.close.assert_awaited_once()

    async def test_execute_propagates_tool_runtime_error(self):
        """MCP tool errors should propagate without being swallowed."""
        tool = SimpleNamespace(
            name="search",
            run_async=AsyncMock(side_effect=RuntimeError("connection refused")),
        )
        toolset = _make_toolset([tool])
        ctx = SimpleNamespace()
        action = _build_action(toolset, selected_tool_name="search", req_src_node="prev-node", ctx=ctx)

        state = {"node_responses": {"prev-node": {"q": "test"}}}
        with pytest.raises(RuntimeError, match="connection refused"):
            await action.execute(state)

    async def test_execute_fails_when_tool_not_found(self):
        """Execute should fail if selected_tool_name doesn't match any tool."""
        toolset = _make_toolset([_make_tool("other", "x")])
        ctx = SimpleNamespace()
        action = _build_action(toolset, selected_tool_name="missing", req_src_node="prev-node", ctx=ctx)

        state = {"node_responses": {"prev-node": {}}}
        with pytest.raises(ValueError, match="cannot find selected tool"):
            await action.execute(state)

    async def test_execute_fails_when_request_args_invalid(self):
        """Execute should fail fast when source node payload is invalid."""
        toolset = _make_toolset([_make_tool("search", "ok")])
        ctx = SimpleNamespace()
        action = _build_action(toolset, selected_tool_name="search", req_src_node="missing", ctx=ctx)

        state = {"node_responses": {}}
        with pytest.raises(ValueError, match="missing"):
            await action.execute(state)
