# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterHandleType
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools import BaseTool


@register_tool_filter("tool_filter")
class ToolFilter(BaseFilter):
    """Tool filter."""

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        print(f"\n\n==== run tool filter run start ===")
        # .. run before
        rsp = await handle()
        # .. run after
        print(f"\n\n==== run tool filter run end ===")
        return rsp


def before_tool_callback(context: InvocationContext, tool: BaseTool, args: dict, response: Any):
    print(
        f'@before_tool_callback context: {type(context)}, tool: {type(tool)}, args: {type(args)}, response: {type(response)}'
    )


def after_tool_callback(context: InvocationContext, tool: BaseTool, args: dict, response: Any):
    print(
        f'@after_tool_callback context: {type(context)}, tool: {type(tool)}, args: {type(args)}, response: {type(response)}'
    )
