# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TRPC Agent Base Class Module.

This module defines the BaseAgent class which serves as the foundation for all
agent implementations in the TRPC Agent Development Kit.

Key Features:
    - Core agent lifecycle management
    - Filter pipeline execution
    - Context propagation
    - Sub-agent hierarchy management
    - Callback handling (before/after execution)

Classes:
    BaseAgent: Abstract base class providing core agent functionality
"""

from __future__ import annotations

from abc import abstractmethod
from functools import partial
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Union
from typing import final
from typing_extensions import override

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.context import reset_invocation_ctx
from trpc_agent_sdk.context import set_invocation_ctx
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.filter import get_filter
from trpc_agent_sdk.filter import run_stream_filters

from ._callback import AgentCallback
from ._callback import AgentCallbackFilter

# Type aliases for instruction providers
InstructionProvider = Callable[[InvocationContext], Union[str, Awaitable[str]]]


def _build_action_string_from_events(events: list[Event], max_length: int = 500) -> str:
    """Build formatted action string from agent events.

    Parses event content to extract and format all actions including:
    - Text responses
    - Function calls
    - Function responses
    - Thoughts

    Args:
        events: List of non-partial events to process
        max_length: Maximum length for function call/response text (default 500)

    Returns:
        Formatted string representing all agent actions
    """
    action_parts = []

    for event in events:
        if not event.content or not event.content.parts:
            continue

        for part in event.content.parts:
            # Handle text content
            if part.text:
                action_parts.append(part.text)

            # Handle thought content
            if part.thought:
                action_parts.append(f"[Thought: {part.thought}]")

            # Handle function call
            if part.function_call:
                func_name = part.function_call.name
                func_args = str(part.function_call.args)
                # Limit function args length
                if len(func_args) > max_length:
                    func_args = func_args[:max_length] + "..."
                action_parts.append(f"[Function Call: {func_name}({func_args})]")

            # Handle function response
            if part.function_response:
                func_name = part.function_response.name
                func_response = str(part.function_response.response)
                # Limit response length
                if len(func_response) > max_length:
                    func_response = func_response[:max_length] + "..."
                action_parts.append(f"[Function Response ({func_name}): {func_response}]")

    return "\n\n".join(action_parts)


class BaseAgent(AgentABC):
    """Base class for all agents in Agent Development Kit.

    Provides core functionality for agent execution including:
    - Filter management and execution
    - Asynchronous operation handling
    - Context management
    - Agent hierarchy management

    Attributes:
        name: The agent's name, must be a Python identifier and unique within the agent tree
        description: Description about the agent's capability
        parent_agent: The parent agent of this agent
        sub_agents: The sub-agents of this agent
        filters_name: List of filter names that will be applied during agent execution
    """

    before_agent_callback: Optional[AgentCallback] = None
    """Callback or list of callbacks to be invoked before the agent run.

    When a list of callbacks is provided, the callbacks will be called in the
    order they are listed until a callback does not return None.

    Args:
      invocation_context: MUST be named 'invocation_context' (enforced).

    Returns:
      Optional[types.Content]: The content to return to the user.
        When the content is present, the agent run will be skipped and the
        provided content will be returned to user.
    """
    after_agent_callback: Optional[AgentCallback] = None
    """Callback or list of callbacks to be invoked after the agent run.

    When a list of callbacks is provided, the callbacks will be called in the
    order they are listed until a callback does not return None.

    Args:
      invocation_context: MUST be named 'invocation_context' (enforced).

    Returns:
      Optional[types.Content]: The content to return to the user.
        When the content is present, the provided content will be used as agent
        response and appended to event history as agent response.
    """

    global_instruction: Union[str, InstructionProvider] = ""
    """Instructions for all agents in the entire agent tree.

    ONLY the global_instruction in root agent will take effect.
    Used to establish consistent personality or behavior across all agents.
    """

    code_executor: Optional[BaseCodeExecutor] = None
    """Allow agent to execute code blocks from model responses using the provided
    CodeExecutor.

    Check out available code executions in `trpc_agent_sdk.code_executors` package.

    NOTE:
        To use model's built-in code executor, use the `BuiltInCodeExecutor`.
    """

    @override
    def get_subagents(self) -> list[AgentABC]:
        """Return sub_agents as the list used for lookup. Override in subclasses if needed."""
        return list(self.sub_agents)

    @override
    def model_post_init(self, __context: Any) -> None:
        """Post init hook for agent."""
        for filter_name in self.filters_name:
            filter_instance = get_filter(FilterType.AGENT, filter_name)
            if not filter_instance:
                raise ValueError(f"Filter {filter_name} not found")
            self.filters.append(filter_instance)
        self.filters.append(AgentCallbackFilter(self.before_agent_callback, self.after_agent_callback))
        return super().model_post_init(__context)

    def _create_invocation_context(self, parent_context: InvocationContext) -> InvocationContext:
        """Creates a new invocation context for this agent."""
        invocation_context = parent_context.model_copy(update={"agent": self})

        # Handle branch assignment:
        # - If parent_context.agent is the same as self, we're being called from runner
        #   and branch is already set correctly, so don't modify it
        # - Otherwise, we're a sub-agent and need to append our name to parent's branch
        if parent_context.agent == self:
            # Called from runner - branch already set correctly
            pass
        elif parent_context.branch:
            # Sub-agent - append our name to parent's branch
            invocation_context.branch = f"{parent_context.branch}.{self.name}"
        else:
            # Fallback: no branch set, initialize with agent name
            invocation_context.branch = self.name

        return invocation_context

    @final
    @override
    async def run_async(
        self,
        parent_context: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Entry point for text-based agent execution.

        Main execution flow:
        1. Setup filters
        2. Create invocation context
        3. Run filters and agent implementation
        4. Yield events

        Args:
            parent_context: Context from parent agent with:
                - Agent reference
                - Invocation ID
                - Branch info

        Yields:
            Event: Agent output events including:
                - Content updates
                - State changes
                - Actions
        """
        from trpc_agent_sdk.telemetry._trace import tracer
        from trpc_agent_sdk.telemetry._trace import trace_agent

        # Avoid start_as_current_span in async generators; cancellation may close
        # the generator from another context and trigger detach token errors.
        span = tracer.start_span(f"agent_run [{self.name}]")
        try:
            ctx = self._create_invocation_context(parent_context)
            if ctx.agent_context is None:
                ctx.agent_context = create_agent_context()
            handle = partial(self._run_async_impl, ctx)  # type: ignore
            token = set_invocation_ctx(ctx)

            # Capture state before agent run
            state_begin = dict(ctx.session.state)

            # Track all non-partial events for building action trace
            non_partial_events = []

            try:
                gen_co = run_stream_filters(ctx.agent_context, None, self.filters, handle)  # type: ignore
                async for event in gen_co:
                    if not event.partial and event.content is not None:
                        # Collect non-partial events with content for tracing
                        # This excludes state update events which have content=None
                        non_partial_events.append(event)
                    yield event  # type: ignore
            finally:
                # Compute state after agent run
                state_end = dict(ctx.session.state)

                # Build formatted action string from all non-partial events
                agent_action = _build_action_string_from_events(non_partial_events)

                # Call trace function with agent execution details
                trace_agent(
                    invocation_context=ctx,
                    agent_action=agent_action,
                    state_begin=state_begin,
                    state_end=state_end,
                )
                # avoid memory leak
                reset_invocation_ctx(token)
        finally:
            span.end()

    @abstractmethod
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Core logic to run this agent via text-based conversation.

        Args:
          ctx: InvocationContext, the invocation context for this agent.

        Yields:
          Event: the events generated by the agent.
        """
        raise NotImplementedError(f"_run_async_impl for {type(self)} is not implemented.")
        # yield  # AsyncGenerator requires having at least one yield statement
