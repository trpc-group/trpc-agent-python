# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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

import asyncio
import time
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

from opentelemetry import trace

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.context import reset_invocation_ctx
from trpc_agent_sdk.context import set_invocation_ctx
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunLimitException
from trpc_agent_sdk.filter import get_filter
from trpc_agent_sdk.filter import run_stream_filters
from trpc_agent_sdk.telemetry import report_invoke_agent
from trpc_agent_sdk.telemetry import tracer
from trpc_agent_sdk.telemetry import trace_agent

from ._callback import AgentCallback
from ._callback import AgentCallbackFilter

# Type aliases for instruction providers
InstructionProvider = Callable[[InvocationContext], Union[str, Awaitable[str]]]


def _aggregate_llm_usage(events: list[Event]) -> tuple[int, int]:
    """Sum prompt/completion tokens across LLM events during one agent run.

    Agent-level metrics (``GenAIInvokeAgent``) roll up the token usage of every
    LLM call performed during the agent run. Each non-partial ``Event`` produced
    by an LLM carries a :class:`GenerateContentResponseUsageMetadata` with the
    cumulative counts for that single model call.

    Args:
        events: Non-partial events collected during the agent run.

    Returns:
        Tuple of ``(input_tokens, output_tokens)``.
    """
    input_tokens = 0
    output_tokens = 0
    for event in events:
        usage = getattr(event, "usage_metadata", None)
        if usage is None:
            continue
        prompt = getattr(usage, "prompt_token_count", None) or 0
        total = getattr(usage, "total_token_count", None) or 0
        if prompt and total:
            input_tokens += prompt
            output_tokens += max(total - prompt, 0)
    return input_tokens, output_tokens


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
        invocation_context._reset_run_limit_observed()

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
        # Manually propagate span context using attach/detach instead of
        # start_as_current_span. This ensures child spans (call_llm, execute_tool,
        # etc.) can correctly resolve their parent.
        # We use start_span + attach/detach rather than start_as_current_span
        # because __aexit__ of the context manager is not guaranteed to run when
        # an async generator is cancelled, but try/finally always executes
        # even under CancelledError (PEP 492).
        with tracer.start_as_current_span(f"agent_run [{self.name}]") as agent_span:
            ctx = self._create_invocation_context(parent_context)
            if ctx.agent_context is None:
                ctx.agent_context = create_agent_context()
            handle = partial(self._run_async_impl, ctx)  # type: ignore
            token = set_invocation_ctx(ctx)

            # Capture state before agent run
            state_begin = dict(ctx.session.state)

            # Track all non-partial events for building action trace
            non_partial_events = []

            # Track accumulated partial text as it streams in (mirrors the
            # pattern used by LlmProcessor.call_llm_async's
            # _build_interrupted_content). If GeneratorExit/CancelledError
            # fires before any non-partial event exists, non_partial_events
            # is empty and _build_action_string_from_events([]) would
            # otherwise produce "", silently dropping everything that was
            # already streamed to the caller.
            partial_text_parts: list[str] = []

            mono_start = time.monotonic()
            t_first_visible: Optional[float] = None
            error_type: Optional[str] = None
            error_message: Optional[str] = None
            interrupted_partial_text: Optional[str] = None

            try:
                gen_co = run_stream_filters(ctx.agent_context, None, self.filters, handle)  # type: ignore
                async for event in gen_co:
                    if t_first_visible is None and event.has_content():
                        t_first_visible = time.monotonic()
                    if event.partial:
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if part.text:
                                    partial_text_parts.append(part.text)
                    else:
                        # A non-partial event finalizes this turn's output;
                        # any partial text accumulated so far has now been
                        # superseded by it, so drop it to avoid duplicating
                        # output already captured in non_partial_events.
                        partial_text_parts.clear()
                        if event.content is not None:
                            # Collect non-partial events with content for tracing
                            # This excludes state update events which have content=None
                            non_partial_events.append(event)
                    yield event  # type: ignore
            except GeneratorExit:
                error_type = "AgentGeneratorExit"
                error_message = "Agent execution stopped with GeneratorExit."
                interrupted_partial_text = "".join(partial_text_parts)
                raise
            except asyncio.CancelledError:
                # Like GeneratorExit, asyncio.CancelledError subclasses
                # BaseException, not Exception, so it is not caught by
                # `except Exception` below. External cancellation
                # (task.cancel(), asyncio.wait_for() timeout, ASGI
                # disconnect) surfaces here as this exception; salvage the
                # partial text the same way as the GeneratorExit branch.
                error_type = "AgentCancelledError"
                error_message = "Agent execution stopped with asyncio.CancelledError."
                interrupted_partial_text = "".join(partial_text_parts)
                raise
            except RunLimitException as ex:
                error_type = ex.error_code
                error_message = str(ex)
                raise
            except Exception as ex:
                error_type = type(ex).__name__
                error_message = str(ex)
                raise
            finally:
                # Compute state after agent run
                state_end = dict(ctx.session.state)

                # Build formatted action string from all non-partial events.
                # Fall back to the accumulated (but never finalized) partial
                # text when the run was interrupted before producing any
                # non-partial event, so streamed output is not silently lost.
                agent_action = _build_action_string_from_events(non_partial_events)
                if not agent_action and interrupted_partial_text:
                    agent_action = f"[INTERRUPTED]\n{interrupted_partial_text}"

                # Call trace function with agent execution details
                with trace.use_span(agent_span, end_on_exit=False):
                    trace_agent(
                        invocation_context=ctx,
                        agent_action=agent_action,
                        state_begin=state_begin,
                        state_end=state_end,
                        error_type=error_type,
                        error_message=error_message,
                    )

                duration_s = time.monotonic() - mono_start
                ttft_s = (t_first_visible - mono_start) if t_first_visible is not None else duration_s
                input_tokens, output_tokens = _aggregate_llm_usage(non_partial_events)
                is_stream = bool(ctx.run_config and ctx.run_config.streaming)
                report_invoke_agent(
                    ctx,
                    duration_s=duration_s,
                    ttft_s=ttft_s,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    is_stream=is_stream,
                    error_type=error_type,
                )

                # avoid memory leak
                reset_invocation_ctx(token)

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
