# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Run filter for TRPC Agent framework."""

from functools import partial
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable

from trpc_agent_sdk.abc import FilterAsyncGenReturnType
from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import FilterReturnType
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.log import logger

from ._base_filter import BaseFilter

AgentFilterHandleType = Callable[[], Awaitable[Any]]
AgentFilterAsyncGenHandleType = Callable[[], AsyncGenerator[Any, None]]  # type: ignore


async def stream_handler_adapter(func: AgentFilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
    """Adapter for agent filter async gen handle."""
    async for event in func():
        if isinstance(event, FilterResult):
            yield event
        else:
            yield FilterResult(rsp=event, is_continue=True)


async def run_stream_filters(ctx: AgentContext, req: Any, filters: list[BaseFilter],
                             handle: AgentFilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
    """Run a sequence of filters on a request.

    Args:
        ctx: Context object for filter execution
        req: Request object for filter execution
        filters: List of filters to execute
        handle: Final handler to call after all filters

    Returns:
        Result of the filter chain execution

    Raises:
        ValueError: If handle is not provided
    """
    if handle is None:
        raise ValueError("handle must be provided")
    current_handle = partial(stream_handler_adapter, handle)
    filters.reverse()
    for filter in filters:
        current_handle = partial(filter.run_stream, ctx, req, current_handle)
    async for event in current_handle():
        yield event.rsp


async def coroutine_handler_adapter(func: AgentFilterHandleType) -> FilterResult:
    """Adapter for agent filter handle."""
    try:
        result = await func()
    except Exception as ex:  # pylint: disable=broad-except
        return FilterResult(error=ex, is_continue=False)

    rsp = None
    error = None
    if isinstance(result, FilterResult):
        return result
    if isinstance(result, tuple) and len(result) == 2:
        rsp, error = result
    else:
        rsp = result

    return FilterResult(rsp=rsp, error=error)


async def run_filters(ctx: AgentContext, req: Any, filters: list[BaseFilter],
                      handle: AgentFilterHandleType) -> FilterReturnType:
    """Run a sequence of filters on a request.

    Args:
        ctx: Context object for filter execution
        req: Request object for filter execution
        filters: List of filters to execute
        handle: Final handler to call after all filters

    Returns:
        Result of the filter chain execution

    Raises:
        ValueError: If handle is not provided
    """
    if handle is None:
        raise ValueError("handle must be provided")
    filters.reverse()
    current_handle = partial(coroutine_handler_adapter, handle)
    for filter in filters:
        current_handle = partial(filter.run, ctx, req, current_handle)
    rsp, error = await current_handle()
    if error:
        logger.error("run_filters error: %s", error)
        raise error
    return rsp
