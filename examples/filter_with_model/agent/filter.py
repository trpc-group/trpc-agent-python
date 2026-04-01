# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterAsyncGenHandleType
from trpc_agent_sdk.filter import FilterAsyncGenReturnType
from trpc_agent_sdk.filter import register_model_filter
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse


@register_model_filter("model_filter")
class ModelFilter(BaseFilter):
    """Model filter."""

    async def run_stream(self, ctx: AgentContext, req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        print(f"\n\n==== run model filter run_stream start ===")
        async for event in handle():
            print(f"\n\n==== run model filter run_stream event ===")
            yield event
            if not event.is_continue:
                print(f"\n\n==== run model filter run_stream end ===")
                return
        print(f"\n\n==== run model filter run_stream end ===")


async def before_model_callback(context: InvocationContext, llm_request: LlmRequest):
    print(f'@before_model_callback context: {type(context)}, llm_request: {type(llm_request)}')
    return None


async def after_model_callback(context: InvocationContext, llm_response: LlmResponse):
    print(f'@after_model_callback context: {type(context)}, llm_response: {type(llm_response)}')
    return None
