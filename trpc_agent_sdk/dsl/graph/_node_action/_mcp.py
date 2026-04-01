# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""MCP node action executor."""

import json
from typing import Any
from typing import Optional
from typing import cast

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import MCPToolset

from .._constants import STATE_KEY_LAST_RESPONSE
from .._constants import STATE_KEY_NODE_RESPONSES
from .._event_writer import AsyncEventWriter
from .._event_writer import EventWriter
from .._state import State
from ._base import BaseNodeAction


class MCPNodeAction(BaseNodeAction):
    """Execute a selected MCP tool with request args from previous node response."""

    def __init__(
        self,
        name: str,
        mcp_toolset: MCPToolset,
        selected_tool_name: str,
        req_src_node: str,
        writer: EventWriter,
        async_writer: AsyncEventWriter,
        ctx: Optional[InvocationContext] = None,
    ):
        super().__init__(name, writer, async_writer, ctx)
        self.mcp_toolset = mcp_toolset
        self.selected_tool_name = selected_tool_name.strip()
        self.req_src_node = req_src_node.strip()

    async def _resolve_selected_tool(self, ctx: InvocationContext) -> BaseTool:
        tools = await self.mcp_toolset.get_tools(invocation_context=ctx)
        for tool in tools:
            if tool.name == self.selected_tool_name:
                return tool

        raise ValueError(
            f"MCP node '{self.name}' cannot find selected tool '{self.selected_tool_name}' in configured MCPToolset.")

    @staticmethod
    def _try_parse_json(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value

    def _resolve_request_args(self, state: State) -> dict[str, Any]:
        node_responses = state[STATE_KEY_NODE_RESPONSES]
        if not isinstance(node_responses, dict):
            raise ValueError(f"MCP node '{self.name}' expects state[{STATE_KEY_NODE_RESPONSES!r}] to be a dict, "
                             f"got {type(node_responses).__name__}.")

        if self.req_src_node not in node_responses:
            raise ValueError(f"MCP node '{self.name}' expects request payload in "
                             f"state[{STATE_KEY_NODE_RESPONSES!r}][{self.req_src_node!r}], but it is missing.")

        request_args = node_responses[self.req_src_node]
        if not isinstance(request_args, dict):
            raise ValueError(f"MCP node '{self.name}' expects request payload from node '{self.req_src_node}' "
                             f"to be a dict, got {type(request_args).__name__}.")

        return request_args

    async def execute(self, state: State) -> dict[str, Any]:
        ctx = cast(InvocationContext, self.ctx)

        request_args = self._resolve_request_args(state)
        selected_tool = await self._resolve_selected_tool(ctx)

        # Do not swallow MCP execution errors: fail fast so users can see invalid requests.
        raw_response = await selected_tool.run_async(tool_context=ctx, args=request_args)
        # Always close mcp resources after invoke
        await self.mcp_toolset.close()
        normalized_response = self._try_parse_json(raw_response)

        return {
            STATE_KEY_LAST_RESPONSE: raw_response,
            STATE_KEY_NODE_RESPONSES: {
                self.name: normalized_response
            },
        }
