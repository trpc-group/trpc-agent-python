# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterAsyncGenHandleType
from trpc_agent_sdk.filter import FilterAsyncGenReturnType
from trpc_agent_sdk.filter import register_agent_filter


@register_agent_filter("agent_filter")
class AgentFilter(BaseFilter):
    """Agent filter."""

    async def run_stream(self, ctx: AgentContext, req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        print(f"\n\n==== run agent filter run_stream start ===")
        async for event in handle():
            print(f"\n\n==== run agent filter run_stream event ===")
            yield event
            if not event.is_continue:
                print(f"\n\n==== run agent filter run_stream end ===")
                return
        print(f"\n\n==== run agent filter run_stream end ===")


async def before_agent_callback(context: InvocationContext):
    print(f'@before_agent_callback context: {type(context)}')
    return None


async def after_agent_callback(context: InvocationContext):
    print(f'@after_agent_callback context: {type(context)}')
    return None
