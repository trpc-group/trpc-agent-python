# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""GraphAgent executor for TRPC-Agent.

This module provides the GraphAgent class that executes compiled StateGraphs
as TRPC-Agent agents, integrating with the Runner and event streaming system.

Enhanced to support graph execution events.
"""

import asyncio
import json
from datetime import datetime
from typing import Any
from typing import AsyncGenerator
from typing import ClassVar
from typing import Optional
from typing import Union
from typing import cast
from typing_extensions import override

from langgraph.types import Command
from langgraph.types import Interrupt
from pydantic import ConfigDict
from pydantic import model_validator

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from ._constants import METADATA_KEY_AGENT_NAME
from ._constants import METADATA_KEY_BRANCH
from ._constants import METADATA_KEY_INVOCATION_ID
from ._constants import METADATA_KEY_SESSION_ID
from ._constants import ROLE_MODEL
from ._constants import ROLE_USER
from ._constants import STATE_KEY_LAST_RESPONSE
from ._constants import STATE_KEY_LONG_RUNNING_PREFIX
from ._constants import STATE_KEY_MESSAGES
from ._constants import STATE_KEY_METADATA
from ._constants import STATE_KEY_NODE_RESPONSES
from ._constants import STATE_KEY_PENDING_INTERRUPT
from ._constants import STATE_KEY_PENDING_INTERRUPT_AUTHOR
from ._constants import STATE_KEY_PENDING_INTERRUPT_BRANCH
from ._constants import STATE_KEY_PENDING_INTERRUPT_ID
from ._constants import STATE_KEY_SESSION
from ._constants import STATE_KEY_USER_INPUT
from ._constants import STREAM_KEY_ACK
from ._constants import STREAM_KEY_EVENT
from ._constants import is_unsafe_state_key
from ._events import EventBuilder
from ._memory_saver import has_graph_internal_checkpoint_state
from ._memory_saver import strip_graph_internal_checkpoint_state
from ._state_graph import CompiledStateGraph

_INTERRUPT_KEY = "__interrupt__"
GraphState = dict[str, Any]
GraphInput = Union[GraphState, Command]
RunnableConfigMap = dict[str, dict[str, Any]]


class GraphAgent(BaseAgent):
    """Agent that executes a compiled StateGraph.

    GraphAgent wraps a CompiledStateGraph and provides integration with
    the TRPC-Agent Runner, handling event streaming and state management.

    Supports:
    - Graph execution completion events
    - Composing nested agents through `StateGraph.add_agent_node(...)`

    Example:
        >>> from trpc_agent_sdk.dsl.graph import StateGraph, GraphAgent, State, START, END
        >>>
        >>> class MyState(State):
        ...     result: str
        >>>
        >>> async def process(state: MyState) -> dict:
        ...     return {"result": "processed"}
        >>>
        >>> graph = StateGraph(MyState)
        >>> graph.add_node("process", process)
        >>> graph.add_edge(START, "process")
        >>> graph.add_edge("process", END)
        >>>
        >>> agent = GraphAgent(
        ...     name="my_workflow",
        ...     description="Processes user requests",
        ...     graph=graph.compile()
        ... )
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Pydantic model config allowing arbitrary types for graph field."""

    sub_agents: ClassVar[tuple[BaseAgent, ...]] = ()
    """GraphAgent does not expose BaseAgent `sub_agents` construction."""

    graph: CompiledStateGraph
    """The compiled StateGraph to execute."""

    @model_validator(mode="before")
    @classmethod
    def _reject_sub_agents(cls, data: Any) -> Any:
        if isinstance(data, dict) and "sub_agents" in data:
            raise ValueError("GraphAgent does not accept `sub_agents`; compose nested agents with "
                             "`StateGraph.add_agent_node(...)`.")
        return data

    @override
    def get_subagents(self) -> list[AgentABC]:
        """Expose graph node agents as direct children for traversal/cleanup."""
        return list(self.graph.source.agent_nodes.values())

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Execute the graph and yield events.

        This method:
        1. Builds initial state from the invocation context
        2. Sets the invocation context on the graph for ctx-signature nodes
        3. Executes the graph with stream_mode=["updates", "custom"]
        4. Yields events emitted by nodes
        5. Emits state update events when state changes
        6. Emits graph execution completion event

        Args:
            ctx: The invocation context from the runner

        Yields:
            Event objects emitted by graph nodes
        """
        # Cancellation checkpoint at method entry
        await ctx.raise_if_cancelled()

        logger.debug(f"[{self.name}] Starting graph execution for invocation {ctx.invocation_id}")

        start_time = datetime.now()
        step_count = 0
        final_state: GraphState = {}
        error_message: Optional[str] = None

        # Create EventBuilder for this execution
        event_builder = EventBuilder(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch or self.name,
        )

        # Build initial state from invocation context
        initial_state = self._build_initial_state(ctx)
        logger.debug(f"[{self.name}] Initial state keys: {list(initial_state.keys())}")

        # Build runnable config with invocation context for thread safety
        runnable_config = self._build_runnable_config(ctx)

        interrupted = False
        resume_command = self._extract_resume_command(ctx)
        if resume_command is not None:
            logger.debug(f"[{self.name}] Resuming graph from pending interrupt")
            self._clear_pending_interrupt_state(ctx)
            graph_input: GraphInput = resume_command
        else:
            graph_input = initial_state

        try:
            # Execute with stream_mode=["updates", "custom"]
            # "updates" is required for state to propagate between nodes
            # "custom" enables EventEmitter streaming
            async for mode, chunk in self.graph.astream(
                    graph_input,
                    runnable_config,
                    stream_mode=["updates", "custom"],
            ):
                # Cancellation checkpoint at each iteration
                await ctx.raise_if_cancelled()

                if isinstance(chunk, dict):
                    interrupts = self._iter_interrupts(chunk)
                else:
                    interrupts = []
                if interrupts:
                    interrupted = True
                    for interrupt in interrupts:
                        function_call_event, function_response_event, long_running_event = (
                            self._create_interrupt_events(ctx, interrupt))
                        yield function_call_event
                        await ctx.raise_if_cancelled()
                        yield function_response_event
                        await ctx.raise_if_cancelled()
                        yield long_running_event
                        await ctx.raise_if_cancelled()
                    continue

                if mode == "updates":
                    # Track state updates and emit state update events
                    if isinstance(chunk, dict):
                        # Normal state updates
                        updated_keys = []
                        for node_name, node_output in chunk.items():
                            if isinstance(node_output, dict):
                                # Collect updated keys
                                updated_keys.extend(key for key in node_output.keys() if isinstance(key, str))
                                # Update final state
                                for key, value in node_output.items():
                                    if isinstance(key, str):
                                        final_state[key] = value
                                logger.debug(
                                    f"[{self.name}] Node '{node_name}' updated keys: {list(node_output.keys())}")

                        # Emit state update event
                        if updated_keys:
                            state_update_event = event_builder.state_update(
                                updated_keys=updated_keys,
                                state_size=len(final_state),
                            )
                            yield state_update_event

                        step_count += 1
                elif mode == "custom":
                    # Yield events from custom stream
                    if isinstance(chunk, dict):
                        ack = chunk.get(STREAM_KEY_ACK)
                        if isinstance(ack, asyncio.Future):
                            if not ack.done():
                                ack.set_result(True)
                        chunk_event = chunk.get(STREAM_KEY_EVENT)
                        if isinstance(chunk_event, Event):
                            yield chunk_event
                            # Cancellation checkpoint after yielding events
                            await ctx.raise_if_cancelled()
        except Exception as e:
            error_message = str(e)
            logger.error(f"[{self.name}] Graph execution failed: {e}", exc_info=True)
        finally:
            # Interrupt pauses graph execution and should not emit graph completion.
            if interrupted and error_message is None:
                logger.debug(f"[{self.name}] Graph execution interrupted and waiting for resume")
                return

            # Emit graph completion event.
            safe_state = self._filter_safe_state(final_state)
            completion_event = event_builder.graph_complete(
                total_steps=step_count,
                start_time=start_time,
                final_state=safe_state,
                state_delta=safe_state,
                error=error_message,
            )
            if completion_event.actions is not None:
                # Persist any state deltas accumulated via InvocationContext.state.
                # This is the primary persistence path for MemorySaver.
                if ctx.actions and ctx.actions.state_delta:
                    completion_event.actions.state_delta.update(ctx.actions.state_delta)
                completion_event.actions.state_delta.update(safe_state)
            logger.debug(f"[{self.name}] Graph execution completed in {step_count} steps")
            yield completion_event

    def _extract_resume_command(self, ctx: InvocationContext) -> Optional[Command]:
        """Build resume command when the latest user event is a function response."""
        events = getattr(ctx.session, "events", [])
        if not events:
            return None

        last_event = events[-1]
        if last_event.author != ROLE_USER:
            return None

        function_responses = last_event.get_function_responses()
        if not function_responses:
            return None

        function_response = function_responses[0]
        function_response_id = function_response.id
        if not isinstance(function_response_id, str) or not function_response_id:
            return None

        session_state = dict(ctx.session.state) if ctx.session.state else {}
        pending_id = session_state.get(STATE_KEY_PENDING_INTERRUPT_ID)
        if isinstance(pending_id, str) and pending_id:
            if function_response_id != pending_id:
                return None

        if not function_response_id.startswith(STATE_KEY_LONG_RUNNING_PREFIX):
            return None
        interrupt_id = function_response_id[len(STATE_KEY_LONG_RUNNING_PREFIX):]
        if not interrupt_id:
            return None

        return Command(resume={interrupt_id: function_response.response})

    def _clear_pending_interrupt_state(self, ctx: InvocationContext) -> None:
        """Clear interrupt marker state before resuming graph execution."""
        ctx.state[STATE_KEY_PENDING_INTERRUPT] = False
        ctx.state[STATE_KEY_PENDING_INTERRUPT_ID] = None
        ctx.state[STATE_KEY_PENDING_INTERRUPT_AUTHOR] = None
        ctx.state[STATE_KEY_PENDING_INTERRUPT_BRANCH] = None

    @staticmethod
    def _iter_interrupts(chunk: dict[str, Any]) -> list[Interrupt]:
        """Extract interrupt objects from a stream chunk."""
        interrupt_data = chunk.get(_INTERRUPT_KEY)
        if interrupt_data is None:
            return []
        if isinstance(interrupt_data, Interrupt):
            return [interrupt_data]
        if isinstance(interrupt_data, (list, tuple)):
            return [item for item in interrupt_data if isinstance(item, Interrupt)]
        return []

    def _create_interrupt_events(
        self,
        ctx: InvocationContext,
        interrupt: Interrupt,
    ) -> tuple[Event, Event, LongRunningEvent]:
        """Create TRPC-native interrupt bridge events for graph pause/resume."""
        function_call_id, function_name, function_args = self._build_interrupt_function(interrupt)
        pending_delta = {
            STATE_KEY_PENDING_INTERRUPT: True,
            STATE_KEY_PENDING_INTERRUPT_ID: function_call_id,
            STATE_KEY_PENDING_INTERRUPT_AUTHOR: self.name,
            STATE_KEY_PENDING_INTERRUPT_BRANCH: ctx.branch or self.name,
        }
        for key, value in pending_delta.items():
            ctx.state[key] = value

        function_call = FunctionCall(
            id=function_call_id,
            name=function_name,
            args=function_args,
        )
        function_call_event = Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=Content(
                role=ROLE_MODEL,
                parts=[Part(function_call=function_call)],
            ),
        )
        function_call_event.long_running_tool_ids = {function_call.id}
        if function_call_event.actions is not None and ctx.actions and ctx.actions.state_delta:
            function_call_event.actions.state_delta.update(ctx.actions.state_delta)

        interrupt_response: dict[str, Any]
        if isinstance(interrupt.value, dict):
            interrupt_response = interrupt.value
        else:
            interrupt_response = {"desicion": interrupt.value}

        function_response = FunctionResponse(
            id=function_call.id,
            name=function_call.name,
            response=interrupt_response,
        )
        function_response_event = Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=Content(
                role=ROLE_USER,
                parts=[Part(function_response=function_response)],
            ),
        )

        long_running_event = LongRunningEvent(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            function_call=function_call,
            function_response=function_response,
        )

        return function_call_event, function_response_event, long_running_event

    def _build_interrupt_function(self, interrupt: Interrupt) -> tuple[str, str, dict[str, Any]]:
        """Build synthetic function call identity and args for interrupt bridging."""
        interrupt_id = interrupt.id
        function_name = "graph_interrupt"
        function_call_id = f"{STATE_KEY_LONG_RUNNING_PREFIX}{interrupt_id}"

        raw_args = interrupt.value
        if isinstance(raw_args, dict):
            function_args = {str(key): value for key, value in raw_args.items()}
        else:
            function_args = {"value": raw_args}

        return function_call_id, function_name, function_args

    def _build_initial_state(self, ctx: InvocationContext) -> GraphState:
        """Build initial state from invocation context.

        Extracts STATE_KEY_MESSAGES from session events and identifies the last
        STATE_KEY_USER_INPUT to populate the initial state.

        Args:
            ctx: The invocation context

        Returns:
            Dictionary containing initial state values
        """
        # Find last STATE_KEY_USER_INPUT text first
        user_input = ""
        user_input_event = None
        for event in reversed(ctx.session.events):
            if not event.is_model_visible():
                continue
            if event.author == "user" and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        user_input = part.text
                        user_input_event = event
                        break
                if user_input:
                    break

        state_base = dict(ctx.session.state) if ctx.session.state else {}
        has_saved_checkpoint = has_graph_internal_checkpoint_state(state_base)

        # Bootstrap messages only when there is no saved checkpoint yet.
        # Once checkpoint data exists, LangGraph memory owns message continuity.
        messages = []
        if not has_saved_checkpoint:
            for event in ctx.session.events:
                if not event.is_model_visible():
                    continue
                if event.content:
                    # Skip the user input event - it will be added via STATE_KEY_USER_INPUT
                    if event is user_input_event:
                        continue
                    messages.append(event.content)
        initial_state = cast(GraphState, strip_graph_internal_checkpoint_state(state_base))
        # Never place the live Session object in graph state.
        # The Session can be accessed from InvocationContext (`ctx`) when needed,
        # while state is checkpointed and serialized across steps.
        initial_state.pop(STATE_KEY_SESSION, None)

        if has_saved_checkpoint:
            # Avoid replaying full message history when checkpointing state exists.
            initial_state.pop(STATE_KEY_MESSAGES, None)
        else:
            initial_state[STATE_KEY_MESSAGES] = messages

        initial_state.update({
            STATE_KEY_USER_INPUT: user_input,
            STATE_KEY_METADATA: {
                METADATA_KEY_INVOCATION_ID: ctx.invocation_id,
                METADATA_KEY_BRANCH: ctx.branch or self.name,
                METADATA_KEY_SESSION_ID: ctx.session.id,
                METADATA_KEY_AGENT_NAME: self.name,
            },
        })

        # Built-ins should always exist for node logic consistency.
        initial_state.setdefault(STATE_KEY_LAST_RESPONSE, None)
        initial_state.setdefault(STATE_KEY_NODE_RESPONSES, {})

        return initial_state

    def _build_runnable_config(self, ctx: InvocationContext) -> RunnableConfigMap:
        """Build configuration for graph execution.

        Args:
            ctx: The invocation context

        Returns:
            Configuration dictionary with thread_id and invocation context
        """
        config: RunnableConfigMap = {
            "configurable": {
                "thread_id": ctx.session.id,
                "checkpoint_ns": self.name,
                # Pass invocation context via configurable for thread-safe access in nodes
                "invocation_context": ctx,
            }
        }

        return config

    def _filter_safe_state(self, state: GraphState) -> GraphState:
        """Filter out non-serializable state keys.

        Uses the centralized UNSAFE_STATE_KEYS set from _define.py for
        comprehensive filtering.

        Args:
            state: State dictionary to filter

        Returns:
            Dictionary with only serializable values
        """
        safe_state: GraphState = {}
        dropped_keys = []
        for key, value in state.items():
            if not is_unsafe_state_key(key):
                try:
                    # Quick serialization check
                    json.dumps(value, default=str)
                    safe_state[key] = value
                except (TypeError, ValueError):
                    dropped_keys.append(key)
            else:
                dropped_keys.append(key)

        if dropped_keys:
            logger.debug(f"[{self.name}] Dropped unsafe/non-serializable state keys: {dropped_keys}")

        return safe_state
