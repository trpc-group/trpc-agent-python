# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TRPC Agent Callback Management Module.

This module provides core functionality for managing agent callbacks in TRPC framework,
including pre-invocation (before) and post-invocation (after) callback handlers.

Key Components:
    AgentCallbackFilter: Main filter class for callback processing
    Before/After callbacks: Handlers for pre and post agent invocation
    Event generation: Creates standardized event objects from callback results

Features:
    - Supports both synchronous and asynchronous callbacks
    - Handles callback chaining and execution order
    - Generates standardized event objects
    - Manages callback context and state
"""
import inspect
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Generic
from typing import Optional
from typing import TypeAlias
from typing import TypeVar
from typing import Union
from typing_extensions import override

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import get_tool_var
from trpc_agent_sdk.types import Content

# Type aliases for callback types
SingleAgentCallback: TypeAlias = Callable[[InvocationContext], Union[Awaitable[Optional[Content]], Optional[Content]]]
AgentCallback: TypeAlias = Union[SingleAgentCallback, list[SingleAgentCallback]]

# Type aliases for model callback types
SingleModelCallback: TypeAlias = Callable[[InvocationContext, Union[LlmRequest, LlmResponse]],
                                          Union[Awaitable[Optional[LlmResponse]], Optional[LlmResponse]]]
ModelCallback: TypeAlias = Union[SingleModelCallback, list[SingleModelCallback]]

# Type aliases for tool callback types
SingleToolCallback: TypeAlias = Callable[[InvocationContext, BaseTool, dict[str, Any], dict],
                                         Union[Awaitable[Optional[dict]], Optional[dict]]]
ToolCallback: TypeAlias = Union[SingleToolCallback, list[SingleToolCallback]]

# Define template type variables for callback types
TCallback = TypeVar('TCallback')


class CallbackFilter(BaseFilter, Generic[TCallback]):
    """Filter for handling agent callback operations (generic version)."""

    def __init__(self, filter_type: FilterType, name: str, before_callback: Union[TCallback, list[TCallback]],
                 after_callback: Union[TCallback, list[TCallback]]):
        super().__init__()
        self._type = filter_type
        self._name = name
        self._before_callback: list[TCallback] = self.canonical_callbacks(before_callback)
        self._after_callback: list[TCallback] = self.canonical_callbacks(after_callback)

    @staticmethod
    def canonical_callbacks(callback: Union[TCallback, list[TCallback]]) -> list[TCallback]:
        if not callback:
            return []
        if isinstance(callback, list):
            return callback
        return [callback]


class AgentCallbackFilter(CallbackFilter[SingleAgentCallback]):
    """Filter for handling agent callback operations.

    This filter manages both pre-invocation (before) and post-invocation (after) callbacks
    for agent operations. It ensures proper execution order and handles both synchronous
    and asynchronous callback functions.
    """

    def __init__(self, before_callback: Union[SingleAgentCallback, list[SingleAgentCallback]],
                 after_callback: Union[SingleAgentCallback, list[SingleAgentCallback]]):
        super().__init__(FilterType.AGENT, "agent_callback", before_callback, after_callback)

    @override
    async def _before(self, ctx: AgentContext, _: Any, rsp: FilterResult):
        """Execute pre-invocation callbacks.

        Processes all registered before-agent callbacks in sequence. Handles both
        synchronous and asynchronous callbacks. Stops processing if any callback
        returns content or sets end_invocation flag.

        Args:
            ctx: Invocation context containing agent and request info
            req: The request string to process

        Returns:
            FilterResult containing event data if callbacks produced output,
            None otherwise
        """
        if not self._before_callback:
            return
        invocation_ctx: InvocationContext = get_invocation_ctx()
        agent_name = invocation_ctx.agent.name
        for callback in self._before_callback:
            before_agent_callback_content = callback(invocation_ctx)
            if inspect.isawaitable(before_agent_callback_content):
                before_agent_callback_content = await before_agent_callback_content
            if before_agent_callback_content:
                ret_event = Event(
                    invocation_id=invocation_ctx.invocation_id,
                    author=agent_name,
                    branch=invocation_ctx.branch,
                    content=before_agent_callback_content,
                    actions=invocation_ctx.event_actions,
                )
                invocation_ctx.end_invocation = True
                rsp.rsp = ret_event
                rsp.error = None
                rsp.is_continue = False
                return
        if invocation_ctx.state.has_delta():
            ret_event = Event(
                invocation_id=invocation_ctx.invocation_id,
                author=agent_name,
                branch=invocation_ctx.branch,
                actions=invocation_ctx.event_actions,
            )
            rsp.rsp = ret_event
            rsp.error = None
            rsp.is_continue = True
            return

    @override
    async def _after(self, ctx: AgentContext, _: Any, rsp: FilterResult):
        """Execute post-invocation callbacks.

        Processes all registered after-agent callbacks in sequence. Handles both
        synchronous and asynchronous callbacks. Collects output from all callbacks.

        Args:
            ctx: Invocation context containing agent and request info
            req: The request string that was processed

        Returns:
            FilterResult containing aggregated event data from callbacks,
            None if no callbacks were registered
        """
        if not self._after_callback:
            return  # type: ignore
        ret = None
        invocation_ctx: InvocationContext = get_invocation_ctx()
        agent_name = invocation_ctx.agent.name
        for callback in self._after_callback:
            after_agent_callback_content = callback(invocation_ctx)
            if inspect.isawaitable(after_agent_callback_content):
                after_agent_callback_content = await after_agent_callback_content
            if after_agent_callback_content:
                ret_event = Event(
                    invocation_id=invocation_ctx.invocation_id,
                    author=agent_name,
                    branch=invocation_ctx.branch,
                    content=after_agent_callback_content,
                    actions=invocation_ctx.event_actions,
                )
                rsp.rsp = ret_event
                return
        if invocation_ctx.state.has_delta():
            ret_event = Event(
                invocation_id=invocation_ctx.invocation_id,
                author=agent_name,
                branch=invocation_ctx.branch,
                content=after_agent_callback_content,
                actions=invocation_ctx.event_actions,
            )
            rsp.rsp = ret_event
            return

        return ret


class ModelCallbackFilter(CallbackFilter[SingleModelCallback]):
    """Filter for handling model callback operations.

    This filter manages both pre-invocation (before) and post-invocation (after) callbacks
    for model operations. It ensures proper execution order and handles both synchronous
    and asynchronous callback functions.
    """

    def __init__(self, before_callback: Union[SingleModelCallback, list[SingleModelCallback]],
                 after_callback: Union[SingleModelCallback, list[SingleModelCallback]]):
        super().__init__(FilterType.MODEL, "model_callback", before_callback, after_callback)

    @override
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Execute pre-invocation callbacks.

        Processes all registered before-model callbacks in sequence. Handles both
        synchronous and asynchronous callbacks. Stops processing if any callback
        returns content or sets end_invocation flag.

        Args:
            ctx: Invocation context containing model and request info
            req: The request string to process

        Returns:
            FilterResult containing event data if callbacks produced output,
            None otherwise
        """
        if not self._before_callback:
            return
        invocation_ctx: InvocationContext = get_invocation_ctx()
        for callback in self._before_callback:
            before_model_callback_content = callback(invocation_ctx, req)
            if inspect.isawaitable(before_model_callback_content):
                before_model_callback_content = await before_model_callback_content
            if before_model_callback_content:
                invocation_ctx.end_invocation = True
                rsp.rsp = before_model_callback_content
                rsp.is_continue = False
                rsp.error = None
                return

    @override
    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Execute post-invocation callbacks for every stream.

        Processes all registered after-agent callbacks in sequence. Handles both
        synchronous and asynchronous callbacks. Collects output from all callbacks.

        Args:
            ctx: Invocation context containing agent and request info
            rsp: The filter result

        Returns:
            None
        """
        if not self._after_callback:
            return  # type: ignore
        invocation_ctx: InvocationContext = get_invocation_ctx()
        for callback in self._after_callback:
            after_model_callback_content = callback(invocation_ctx, rsp.rsp)  # type: ignore
            if inspect.isawaitable(after_model_callback_content):
                after_model_callback_content = await after_model_callback_content
            if after_model_callback_content:
                rsp.rsp = after_model_callback_content
                return


class ToolCallbackFilter(CallbackFilter[SingleToolCallback]):
    """Filter for handling tool callback operations.

    This filter manages both pre-invocation (before) and post-invocation (after) callbacks
    for tool operations. It ensures proper execution order and handles both synchronous
    and asynchronous callback functions.
    """

    def __init__(self, before_callback: Union[SingleToolCallback, list[SingleToolCallback]],
                 after_callback: Union[SingleToolCallback, list[SingleToolCallback]]):
        super().__init__(FilterType.TOOL, "tool_callback", before_callback, after_callback)

    @override
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Execute pre-invocation callbacks.

        Processes all registered before-tool callbacks in sequence. Handles both
        synchronous and asynchronous callbacks. Stops processing if any callback
        returns content or sets end_invocation flag.

        Args:
            ctx: Invocation context containing tool and request info
            req: The request string to process

        Returns:
            FilterResult containing event data if callbacks produced output,
            None otherwise
        """
        if not self._before_callback:
            return
        invocation_ctx: InvocationContext = get_invocation_ctx()
        tool = get_tool_var()
        for callback in self._before_callback:
            before_tool_callback_content = callback(invocation_ctx, tool, req, None)  # type: ignore
            if inspect.isawaitable(before_tool_callback_content):
                before_tool_callback_content = await before_tool_callback_content
            if before_tool_callback_content:
                rsp.rsp = before_tool_callback_content
                rsp.is_continue = False
                rsp.error = None
                await self._after(ctx, req, rsp)
                return

    @override
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Execute post-invocation callbacks.

        Processes all registered after-tool callbacks in sequence. Handles both
        synchronous and asynchronous callbacks. Collects output from all callbacks.
        """
        if not self._after_callback:
            return
        invocation_ctx: InvocationContext = get_invocation_ctx()
        tool = get_tool_var()
        for callback in self._after_callback:
            after_tool_callback_content = callback(invocation_ctx, tool, req, rsp.rsp)  # type: ignore
            if inspect.isawaitable(after_tool_callback_content):
                after_tool_callback_content = await after_tool_callback_content
            if after_tool_callback_content:
                rsp.rsp = after_tool_callback_content
                return
