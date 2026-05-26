# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Directly reuse the types from adk-python
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""MCP toolset for TRPC Agent framework."""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import cast
from typing import List
from typing import Optional
from typing import Union
from typing_extensions import override

from mcp import ClientSession
from mcp import types as mcp_types
from mcp.types import ListToolsResult

from trpc_agent_sdk.abc import ToolPredicate
from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger

from .._base_tool import BaseTool
from ..utils import retry_on_closed_resource
from ._mcp_session_manager import MCPSessionManager
from ._mcp_tool import MCPTool
from ._types import McpConnectionParamsType
from ._types import McpStdioServerParameters
from ._utils import convert_conn_params


class MCPToolset(BaseToolSet):
    """Connects to a MCP Server, and retrieves MCP Tools into TrpcAgent Tools.

    This toolset manages the connection to an MCP server and provides tools
    that can be used by an agent. It properly implements the BaseToolset
    interface for easy integration with the agent framework.

    Usage::
      mcp_toolset = MCPToolset(
          connection_params=StdioConnectionParams(
              command='npx',
              args=["-y", "@modelcontextprotocol/server-filesystem"],
          ),
          tool_filter=['read_file', 'list_directory']  # Optional: filter specific tools
      )
      agent = LlmAgent(
          name="async_agent",
          model="deepseek-v3-local-II",
          tools=[mcp_toolset]
      )

      # Cleanup is handled automatically by the agent framework
      # But you can also manually close if needed:
      # await toolset.close()
    """

    def __init__(self,
                 *,
                 connection_params: McpConnectionParamsType | McpStdioServerParameters = None,
                 tool_filter: Optional[Union[ToolPredicate, List[str]]] = None,
                 is_include_all_tools: bool = True,
                 mcp_tool_cls=MCPTool,
                 filters_name: Optional[list[str]] = None,
                 filters: Optional[list[BaseFilter]] = None,
                 session_group_params: Optional[dict] = None,
                 cache_tools: bool = True,
                 tools_cache_ttl: Optional[float] = 60.0):
        """Initializes the MCPToolset.

        Args:
          connection_params: The connection parameters to the MCP server. Can be:
            ``StdioConnectionParams`` for using local mcp server (e.g. using ``npx`` or
            ``python3``); or ``SseConnectionParams`` for a local/remote SSE server; or
            ``StreamableHTTPConnectionParams`` for local/remote Streamable http
            server. Note, ``StdioServerParameters`` is also supported for using local
            mcp server (e.g. using ``npx`` or ``python3`` ), but it does not support
            timeout, and we recommend to use ``StdioConnectionParams`` instead when
            timeout is needed.
          tool_filter: Optional filter to select specific tools. Can be either:
            - A list of tool names to include
            - A ToolPredicate function for custom filtering logic
            list of tool names to include - A ToolPredicate function for custom
            filtering logic
          is_include_all_tools: Whether to include all available tools by default
          mcp_tool_cls: Class to use for creating MCP tool instances
          filters_name: List of filter names to apply to the tools
          filters: List of filter instances to apply to the tools
          session_group_params: Optional parameters for session group management
          cache_tools: Whether to cache the MCP server's list_tools response.
          tools_cache_ttl: Cache lifetime in seconds for MCP servers that do not
            support tools.listChanged notifications. Servers that support
            listChanged use notification-driven invalidation instead.
        """

        super().__init__(tool_filter=tool_filter, is_include_all_tools=is_include_all_tools)

        if tools_cache_ttl is not None and tools_cache_ttl < 0:
            raise ValueError("tools_cache_ttl must be non-negative.")

        self._connection_params = connection_params
        self._mcp_tool_cls = mcp_tool_cls
        # Create the session manager that will handle the MCP connection
        self._mcp_session_manager: MCPSessionManager | None = None
        self._filters = filters
        self._filters_name = filters_name
        self._session_group_params = session_group_params or {}
        self._cache_tools = cache_tools
        self._tools_cache_ttl = tools_cache_ttl
        self._tools_cache_lock = asyncio.Lock()
        self._tools_cache: ListToolsResult | None = None
        self._tools_cache_updated_at: float | None = None

    def _checker_required_params(self):
        """Validates that all required parameters are properly initialized.

        Raises:
            ValueError: If any required parameter is None
        """
        if not self._connection_params:
            raise ValueError("_connection_params is None.")
        if not self._mcp_session_manager:
            raise ValueError("_mcp_session_manager is None.")

    def clear_tools_cache(self) -> None:
        """Clears the cached MCP tool definitions.

        Call this when the MCP server's tool set is known to have changed and
        the next get_tools call should re-query list_tools.
        """
        self._tools_cache = None
        self._tools_cache_updated_at = None

    def _server_supports_tool_list_changed(self, session: ClientSession) -> bool:
        """Returns whether the server can notify client about tool list changes."""
        try:
            get_capabilities = getattr(session, "get_server_capabilities", None)
            if get_capabilities is None:
                return False
            capabilities = get_capabilities()
            if inspect.isawaitable(capabilities):
                close = getattr(capabilities, "close", None)
                if close is not None:
                    close()
                return False
        except Exception:  # pylint: disable=broad-except
            return False

        tools_capability = getattr(capabilities, "tools", None)
        return getattr(tools_capability, "listChanged", False) is True

    def _is_tools_cache_valid(self, session: ClientSession) -> bool:
        """Returns whether the cached list_tools response can be reused."""
        if not self._cache_tools or self._tools_cache is None:
            return False
        if self._server_supports_tool_list_changed(session):
            return True
        if self._tools_cache_ttl is None:
            return False
        if self._tools_cache_updated_at is None:
            return False
        return time.monotonic() - self._tools_cache_updated_at < self._tools_cache_ttl

    async def _get_tools_response(self, session: ClientSession) -> ListToolsResult:
        """Returns MCP tool definitions, using cache when enabled."""
        if not self._cache_tools:
            return await session.list_tools()

        if self._is_tools_cache_valid(session):
            return cast(ListToolsResult, self._tools_cache)

        async with self._tools_cache_lock:
            if self._is_tools_cache_valid(session):
                return cast(ListToolsResult, self._tools_cache)

            tools_response: ListToolsResult = await session.list_tools()
            self._tools_cache = tools_response
            self._tools_cache_updated_at = time.monotonic()
            return tools_response

    def _build_session_group_params(self) -> dict:
        """Builds ClientSession params with tool-change notification handling."""
        params = dict(self._session_group_params)
        if not self._cache_tools:
            return params

        user_message_handler = params.get("message_handler")

        async def message_handler(message):
            if (isinstance(message, mcp_types.ServerNotification)
                    and isinstance(message.root, mcp_types.ToolListChangedNotification)):
                self.clear_tools_cache()

            if user_message_handler is not None:
                await user_message_handler(message)

        params["message_handler"] = message_handler
        return params

    @override
    def initialize(self) -> None:
        """Initialize the toolset."""
        if self._mcp_session_manager:
            return
        super().initialize()
        self._connection_params = convert_conn_params(self._connection_params)
        self._mcp_session_manager = MCPSessionManager(
            connection_params=self._connection_params,
            session_group_params=self._build_session_group_params(),
        )
        self._checker_required_params()

    @retry_on_closed_resource
    async def get_tools(
        self,
        invocation_context: Optional[InvocationContext] = None,
    ) -> List[BaseTool]:
        """Return all tools in the toolset based on the provided context.

        Args:
            invocation_context: Context used to filter tools available to the agent.
                If None, all tools in the toolset are returned.

        Returns:
            List[BaseTool]: A list of tools available under the specified context.
        """
        self.initialize()

        # Get session from session manager
        session = await self._mcp_session_manager.create_session()

        # Fetch available tools from the MCP server
        tools_response = await self._get_tools_response(session)

        # Apply filtering based on context and tool_filter
        tools = []
        for tool in tools_response.tools:
            mcp_tool = self._mcp_tool_cls(
                mcp_tool=tool,
                mcp_session_manager=self._mcp_session_manager,  # type: ignore
                filters_name=self._filters_name,
                filters=self._filters,
            )

            if self._is_tool_selected(mcp_tool, invocation_context):
                tools.append(mcp_tool)
        return tools

    @override
    async def close(self) -> None:
        """Performs cleanup and releases resources held by the toolset.

        This method closes the MCP session and cleans up all associated resources.
        It's designed to be safe to call multiple times and handles cleanup errors
        gracefully to avoid blocking application shutdown.
        """
        try:
            self.clear_tools_cache()
            if self._mcp_session_manager is None:
                return
            await self._mcp_session_manager.close()
        except Exception as ex:  # pylint: disable=broad-except
            # Log the error but don't re-raise to avoid blocking shutdown
            logger.warning("Warning: Error during MCPToolset cleanup: %s", ex)
