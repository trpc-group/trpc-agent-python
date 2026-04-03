# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import StdioServerParameters as McpStdioServerParameters
from mcp.types import ListToolsResult, Tool as McpBaseTool

from trpc_agent_sdk.tools.mcp_tool._mcp_toolset import MCPToolset
from trpc_agent_sdk.tools.mcp_tool._mcp_tool import MCPTool
from trpc_agent_sdk.tools.mcp_tool._mcp_session_manager import MCPSessionManager
from trpc_agent_sdk.tools.mcp_tool._types import StdioConnectionParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stdio_conn():
    return StdioConnectionParams(
        server_params=McpStdioServerParameters(command="echo", args=["hello"]),
    )


# ---------------------------------------------------------------------------
# Tests: __init__
# ---------------------------------------------------------------------------

class TestMCPToolsetInit:
    def test_default_init(self):
        ts = MCPToolset(connection_params=_stdio_conn())
        assert ts._connection_params is not None
        assert ts._mcp_session_manager is None
        assert ts._filters is None
        assert ts._filters_name is None

    def test_with_tool_filter_list(self):
        ts = MCPToolset(connection_params=_stdio_conn(), tool_filter=["tool_a"])
        assert ts._tool_filter == ["tool_a"]

    def test_with_tool_filter_predicate(self):
        pred = lambda tool, ctx=None: True
        ts = MCPToolset(connection_params=_stdio_conn(), tool_filter=pred)
        assert ts._tool_filter is pred

    def test_with_filters(self):
        mock_filter = MagicMock()
        ts = MCPToolset(
            connection_params=_stdio_conn(),
            filters_name=["f1"],
            filters=[mock_filter],
        )
        assert ts._filters_name == ["f1"]
        assert ts._filters == [mock_filter]

    def test_custom_mcp_tool_cls(self):
        custom_cls = MagicMock()
        ts = MCPToolset(connection_params=_stdio_conn(), mcp_tool_cls=custom_cls)
        assert ts._mcp_tool_cls is custom_cls

    def test_session_group_params_default_empty(self):
        ts = MCPToolset(connection_params=_stdio_conn())
        assert ts._session_group_params == {}

    def test_session_group_params_custom(self):
        ts = MCPToolset(connection_params=_stdio_conn(), session_group_params={"key": "val"})
        assert ts._session_group_params == {"key": "val"}


# ---------------------------------------------------------------------------
# Tests: _checker_required_params
# ---------------------------------------------------------------------------

class TestCheckerRequiredParams:
    def test_raises_when_connection_params_none(self):
        ts = MCPToolset()
        ts._connection_params = None
        with pytest.raises(ValueError, match="_connection_params is None"):
            ts._checker_required_params()

    def test_raises_when_session_manager_none(self):
        ts = MCPToolset(connection_params=_stdio_conn())
        ts._mcp_session_manager = None
        with pytest.raises(ValueError, match="_mcp_session_manager is None"):
            ts._checker_required_params()


# ---------------------------------------------------------------------------
# Tests: initialize
# ---------------------------------------------------------------------------

class TestInitialize:
    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_toolset.MCPSessionManager")
    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_toolset.convert_conn_params")
    def test_initialize_creates_session_manager(self, mock_convert, mock_mgr_cls):
        conn = _stdio_conn()
        mock_convert.return_value = conn
        mock_mgr_cls.return_value = MagicMock(spec=MCPSessionManager)

        ts = MCPToolset(connection_params=conn)
        ts.initialize()

        mock_convert.assert_called_once()
        mock_mgr_cls.assert_called_once()
        assert ts._mcp_session_manager is not None

    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_toolset.MCPSessionManager")
    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_toolset.convert_conn_params")
    def test_initialize_idempotent(self, mock_convert, mock_mgr_cls):
        conn = _stdio_conn()
        mock_convert.return_value = conn
        mgr_instance = MagicMock(spec=MCPSessionManager)
        mock_mgr_cls.return_value = mgr_instance

        ts = MCPToolset(connection_params=conn)
        ts.initialize()
        ts.initialize()

        mock_mgr_cls.assert_called_once()

    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_toolset.MCPSessionManager")
    @patch("trpc_agent_sdk.tools.mcp_tool._mcp_toolset.convert_conn_params")
    def test_initialize_with_stdio_server_params(self, mock_convert, mock_mgr_cls):
        """StdioServerParameters should be auto-converted via convert_conn_params."""
        server_params = McpStdioServerParameters(command="npx")
        conn = StdioConnectionParams(server_params=server_params)
        mock_convert.return_value = conn
        mock_mgr_cls.return_value = MagicMock(spec=MCPSessionManager)

        ts = MCPToolset(connection_params=server_params)
        ts.initialize()

        mock_convert.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: get_tools
# ---------------------------------------------------------------------------

class TestGetTools:
    @pytest.mark.asyncio
    async def test_get_tools_returns_all_tools(self):
        ts = MCPToolset(connection_params=_stdio_conn())

        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_session = AsyncMock()
        mock_mgr.create_session = AsyncMock(return_value=mock_session)

        mcp_tools = [
            McpBaseTool(name="tool_a", description="desc_a", inputSchema={"type": "object"}),
            McpBaseTool(name="tool_b", description="desc_b", inputSchema={"type": "object"}),
        ]
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=mcp_tools))

        with patch.object(ts, "initialize") as mock_init:
            ts._mcp_session_manager = mock_mgr
            tools = await ts.get_tools()

        assert len(tools) == 2
        assert all(isinstance(t, MCPTool) for t in tools)
        names = {t.name for t in tools}
        assert names == {"tool_a", "tool_b"}

    @pytest.mark.asyncio
    async def test_get_tools_with_list_filter(self):
        ts = MCPToolset(
            connection_params=_stdio_conn(),
            tool_filter=["tool_a"],
            is_include_all_tools=False,
        )

        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_session = AsyncMock()
        mock_mgr.create_session = AsyncMock(return_value=mock_session)

        mcp_tools = [
            McpBaseTool(name="tool_a", description="desc_a", inputSchema={"type": "object"}),
            McpBaseTool(name="tool_b", description="desc_b", inputSchema={"type": "object"}),
        ]
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=mcp_tools))

        with patch.object(ts, "initialize"):
            ts._mcp_session_manager = mock_mgr
            tools = await ts.get_tools()

        assert len(tools) == 1
        assert tools[0].name == "tool_a"

    @pytest.mark.asyncio
    async def test_get_tools_with_predicate_filter(self):
        pred = lambda tool, ctx=None: tool.name.startswith("allow")
        ts = MCPToolset(
            connection_params=_stdio_conn(),
            tool_filter=pred,
            is_include_all_tools=False,
        )

        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_session = AsyncMock()
        mock_mgr.create_session = AsyncMock(return_value=mock_session)

        mcp_tools = [
            McpBaseTool(name="allow_tool", description="ok", inputSchema={"type": "object"}),
            McpBaseTool(name="deny_tool", description="no", inputSchema={"type": "object"}),
        ]
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=mcp_tools))

        with patch.object(ts, "initialize"):
            ts._mcp_session_manager = mock_mgr
            tools = await ts.get_tools()

        assert len(tools) == 1
        assert tools[0].name == "allow_tool"

    @pytest.mark.asyncio
    async def test_get_tools_empty_server(self):
        ts = MCPToolset(connection_params=_stdio_conn())

        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_session = AsyncMock()
        mock_mgr.create_session = AsyncMock(return_value=mock_session)
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=[]))

        with patch.object(ts, "initialize"):
            ts._mcp_session_manager = mock_mgr
            tools = await ts.get_tools()

        assert tools == []

    @pytest.mark.asyncio
    async def test_get_tools_passes_filters_to_mcp_tool(self):
        mock_filter = MagicMock()
        ts = MCPToolset(
            connection_params=_stdio_conn(),
            filters=[mock_filter],
        )

        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_session = AsyncMock()
        mock_mgr.create_session = AsyncMock(return_value=mock_session)

        mcp_tools = [
            McpBaseTool(name="tool_a", description="desc_a", inputSchema={"type": "object"}),
        ]
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=mcp_tools))

        with patch.object(ts, "initialize"):
            ts._mcp_session_manager = mock_mgr
            tools = await ts.get_tools()

        assert len(tools) == 1
        assert len(tools[0]._filters) == 1

    @pytest.mark.asyncio
    async def test_get_tools_calls_initialize(self):
        ts = MCPToolset(connection_params=_stdio_conn())

        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_session = AsyncMock()
        mock_mgr.create_session = AsyncMock(return_value=mock_session)
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=[]))

        with patch.object(ts, "initialize") as mock_init:
            ts._mcp_session_manager = mock_mgr
            await ts.get_tools()
            mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tools_with_custom_mcp_tool_cls(self):
        custom_cls = MagicMock()
        custom_tool_instance = MagicMock()
        custom_tool_instance.name = "custom"
        custom_cls.return_value = custom_tool_instance

        ts = MCPToolset(connection_params=_stdio_conn(), mcp_tool_cls=custom_cls)

        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_session = AsyncMock()
        mock_mgr.create_session = AsyncMock(return_value=mock_session)

        mcp_tools = [
            McpBaseTool(name="tool_a", description="desc", inputSchema={"type": "object"}),
        ]
        mock_session.list_tools = AsyncMock(return_value=ListToolsResult(tools=mcp_tools))

        with patch.object(ts, "initialize"):
            ts._mcp_session_manager = mock_mgr
            tools = await ts.get_tools()

        custom_cls.assert_called_once()
        assert len(tools) == 1


# ---------------------------------------------------------------------------
# Tests: close
# ---------------------------------------------------------------------------

class TestClose:
    @pytest.mark.asyncio
    async def test_close_delegates_to_session_manager(self):
        ts = MCPToolset(connection_params=_stdio_conn())
        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_mgr.close = AsyncMock()
        ts._mcp_session_manager = mock_mgr

        await ts.close()
        mock_mgr.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_when_no_session_manager(self):
        ts = MCPToolset(connection_params=_stdio_conn())
        ts._mcp_session_manager = None
        await ts.close()

    @pytest.mark.asyncio
    async def test_close_swallows_exception(self):
        ts = MCPToolset(connection_params=_stdio_conn())
        mock_mgr = MagicMock(spec=MCPSessionManager)
        mock_mgr.close = AsyncMock(side_effect=RuntimeError("cleanup error"))
        ts._mcp_session_manager = mock_mgr

        await ts.close()
