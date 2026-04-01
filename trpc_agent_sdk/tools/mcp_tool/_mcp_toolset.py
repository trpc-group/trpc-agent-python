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
"""MCP toolset for TRPC Agent framework."""

from __future__ import annotations

from typing import List
from typing import Optional
from typing import Union
from typing_extensions import override

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
                 session_group_params: Optional[dict] = None):
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
        """

        super().__init__(tool_filter=tool_filter, is_include_all_tools=is_include_all_tools)

        self._connection_params = connection_params
        self._mcp_tool_cls = mcp_tool_cls
        # Create the session manager that will handle the MCP connection
        self._mcp_session_manager: MCPSessionManager | None = None
        self._filters = filters
        self._filters_name = filters_name
        self._session_group_params = session_group_params or {}

    def _checker_required_params(self):
        """Validates that all required parameters are properly initialized.

        Raises:
            ValueError: If any required parameter is None
        """
        if not self._connection_params:
            raise ValueError("_connection_params is None.")
        if not self._mcp_session_manager:
            raise ValueError("_mcp_session_manager is None.")

    @override
    def initialize(self) -> None:
        """Initialize the toolset."""
        if self._mcp_session_manager:
            return
        super().initialize()
        self._connection_params = convert_conn_params(self._connection_params)
        self._mcp_session_manager = MCPSessionManager(
            connection_params=self._connection_params,
            session_group_params=self._session_group_params,
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
        tools_response: ListToolsResult = await session.list_tools()

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
            if self._mcp_session_manager is None:
                return
            await self._mcp_session_manager.close()
        except Exception as ex:  # pylint: disable=broad-except
            # Log the error but don't re-raise to avoid blocking shutdown
            logger.warning("Warning: Error during MCPToolset cleanup: %s", ex)
