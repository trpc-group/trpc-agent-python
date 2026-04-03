# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
TRPC Agent Filter System Core Abstractions.

This module defines the fundamental building blocks for the TRPC Agent filter system,
providing:

1. Base Classes:
   - FilterABC: Abstract base class defining the filter interface
   - FilterResult: Standardized container for filter outputs

2. Type System:
   - FilterType: Enumeration of filter categories
   - Generic type variables for context and request/response types
   - Type aliases for filter handlers and results

3. Core Features:
   - Async-first design with full async generator support
   - Type-safe filter execution pipeline
   - Comprehensive error handling
   - Extensible filter categorization

Example Usage:
    class MyFilter(FilterABC):
        async def _before(self, ctx, req):
            # Pre-processing logic
            yield FilterResult(...)

        async def _after(self, ctx, req):
            # Post-processing logic
            yield FilterResult(...)
"""

from __future__ import annotations

import inspect
from functools import partial
from types import AsyncGeneratorType
from types import CoroutineType
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Union
from typing_extensions import override

from trpc_agent_sdk.abc import FilterABC
from trpc_agent_sdk.abc import FilterAsyncGenHandleType
from trpc_agent_sdk.abc import FilterAsyncGenReturnType
from trpc_agent_sdk.abc import FilterHandleType
from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.log import logger

from ..exceptions import RunCancelledException


class BaseFilter(FilterABC):
    """Abstract base class defining the filter interface.

    All concrete filters must implement these methods to be compatible
    with the filter management system.
    """

    @override
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Execute before.

        Args:
            ctx: AgentContext
            req: Request data
            rsp: Response data, will be used to store the result of the filter

        Returns:
            None
        """
        return None

    @override
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Execute after.

        Args:
            ctx: AgentContext
            req: Request data
            rsp: Response data, will be used to store the result of the filter
        Returns:
            None
        """
        return None

    @override
    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Execute after every stream.

        Args:
            ctx: AgentContext
            req: Request data
            rsp: Response data, will be used to store the result of the filter
        Returns:
            None
        """
        return None

    async def _handle_co(self,
                         result: FilterResult,
                         co: Union[CoroutineType, AsyncGeneratorType],
                         msg: str,
                         handle_event: Callable[[FilterResult], Awaitable[None]] = None) -> FilterAsyncGenReturnType:
        """Execute the before lifecycle.

        Args:
            ctx: Execution context
            req: Request data
            handle: Next handler in the chain
        """
        try:
            if inspect.isasyncgen(co):
                async for event in co:
                    if not isinstance(event, FilterResult):
                        raise TypeError(f"{msg} result must be a FilterResult, got {type(event)}")
                    if handle_event:
                        await handle_event(event)
                    yield event
                    if event.error:
                        # Error passed from upper layer, use debug mode
                        logger.debug(self._create_err_str(f"{msg} error: {event.error}"))
                    if not event.is_continue:
                        return
            else:
                rsp = await co
                if rsp:
                    if isinstance(rsp, tuple) and len(rsp) == 2:
                        result.rsp, result.error = rsp
                        if result.error:
                            result.is_continue = False
                    else:
                        result.rsp = rsp
                if result.rsp:
                    yield result
                    if result.error:
                        # Error passed from upper layer, use debug mode
                        logger.debug(self._create_err_str(f"{msg} error: {result.error}"))
                if not result.is_continue:
                    return
        except RunCancelledException:
            # raise to runner to handle
            raise
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("filter type: %s, name: %s run %s error: %s",
                         self._type.name,
                         self._name,
                         msg,
                         ex,
                         exc_info=True)
            yield FilterResult(error=ex, is_continue=False)
            return

    @override
    async def run_stream(self, ctx: AgentContext, req: Any,
                         handle: FilterAsyncGenHandleType) -> FilterAsyncGenReturnType:
        """Execute the full filter lifecycle (before -> handle -> after).

        Args:
            ctx: Execution context
            req: Request data
            handle: Next handler in the chain

        Returns:
            FilterResult: Combined result of all operations
        """
        result = FilterResult()

        # run before in current filter
        async for event in self._handle_co(result, self._before(ctx, req, result), "before"):
            yield event
            if not event.is_continue:
                return

        # run last filter
        handle_event = partial(self._after_every_stream, ctx, req)
        async for event in self._handle_co(result, handle(), "handle", handle_event):
            yield event
            if not event.is_continue:
                return

        # run after in current filter
        async for event in self._handle_co(result, self._after(ctx, req, result), "after"):
            yield event
            if not event.is_continue:
                return

    @override
    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        """Execute the full filter lifecycle (before -> handle -> after).

        Args:
            ctx: Execution context
            req: Request data
            handle: Next handler in the chain

        Returns:
            FilterResult: Combined result of all operations
        """
        result = FilterResult()
        # 1. before
        try:
            await self._before(ctx, req, result)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error(self._create_err_str(f"run before error: {ex}"))
            return None, ex
        if result.error or not result.is_continue:
            return result

        # 2.last handle
        rsp = await handle()
        if isinstance(rsp, FilterResult):
            result = rsp
        elif isinstance(rsp, tuple) and len(rsp) == 2:
            result.rsp, result.error = rsp
            if result.error:
                result.is_continue = False
        else:
            result.rsp = rsp
        if result.error or not result.is_continue:
            return result

        # 3. after
        try:
            await self._after(ctx, req, result)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error(self._create_err_str(f"run after error: {ex}"))
            result.error = ex
            result.is_continue = False

        return result
