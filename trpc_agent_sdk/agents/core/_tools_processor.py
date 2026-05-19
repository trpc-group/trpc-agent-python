# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools Processor implementation for TRPC Agent framework.

This module provides the ToolsProcessor class which handles tool invocation
and processing for agents. It uses the unified Event system for communication.

The ToolsProcessor is simplified to directly use the unified Event class.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from typing import AsyncGenerator
from typing import List
from typing import Optional
from typing import TypeAlias
from typing import Union

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import EventActions
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.telemetry import report_execute_tool
from trpc_agent_sdk.telemetry import trace_merged_tool_calls
from trpc_agent_sdk.telemetry import trace_tool_call
from trpc_agent_sdk.telemetry import tracer
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import convert_toolunion_to_tool_list
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part

# Type aliases for tool definitions
ToolUnion: TypeAlias = Union[BaseTool, BaseToolSet]


class ToolsProcessor:
    """Tools Processor for handling tool processing and execution.

    This class manages tool declarations for LLM requests and executes
    tools when called by the LLM, yielding appropriate events.
    """

    def __init__(self, tools: list[ToolUnion]):
        """Initialize ToolsProcessor with a list of tools.

        Args:
            tools: List of tools (BaseToolSet)
        """
        self.tools = tools

    async def process_llm_request(self, context: InvocationContext, request: LlmRequest) -> None:
        """Add tool declarations to the LLM request.

        This method processes the available tools and adds their declarations
        to the model request so the LLM can call them. It resolves BaseToolSet
        instances by calling their get_tools() method.

        Additionally, this method dynamically detects streaming tools by checking
        the is_streaming property on resolved tools, and updates the request's
        streaming_tool_names accordingly. This enables proper streaming support
        for tools inside ToolSet instances.

        Args:
            context: The invocation context
            request: The model request to add tools to

        Raises:
            Exception: If tool processing fails
        """
        if not self.tools:
            logger.debug("No tools to add to request")
            return

        try:
            # Resolve tools first - this is where BaseToolSet.get_tools() is called
            resolved_tools = await convert_toolunion_to_tool_list(self.tools, context)

            if resolved_tools:
                # Add tools to the request using append_tools directly
                for tool in resolved_tools:
                    await tool.process_request(tool_context=context, llm_request=request)
                logger.debug("Added %s tool declarations to request", len(resolved_tools))

                # Dynamically detect streaming tools and update request
                self._update_streaming_tool_names(request, resolved_tools)
            else:
                logger.warning("No valid tools to add to request")

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error processing tools: %s", ex, exc_info=True)
            raise Exception(f"Tool processing failed: {str(ex)}") from ex

    def _update_streaming_tool_names(
        self,
        request: LlmRequest,
        resolved_tools: List[BaseTool],
    ) -> None:
        """Update request's streaming_tool_names based on resolved tools.

        This method dynamically detects which tools support streaming by checking
        their is_streaming property. Tools that have is_streaming=True will have
        their arguments streamed during LLM generation.

        Args:
            request: The LLM request to update
            resolved_tools: List of resolved BaseTool instances
        """
        streaming_names = set()
        for tool in resolved_tools:
            if getattr(tool, "is_streaming", False):
                streaming_names.add(tool.name)

        if streaming_names:
            if request.streaming_tool_names is None:
                request.streaming_tool_names = set()
            request.streaming_tool_names.update(streaming_names)
            logger.debug("Detected %d streaming tools: %s", len(streaming_names), streaming_names)

    async def __invoke_tools(
        self,
        context: InvocationContext,
        resolved_tools: List[BaseTool],
        tool_call: FunctionCall,
        function_response_events: list[Event],
    ) -> Event:
        # Find the appropriate tool
        tool = await self._find_tool(tool_call, resolved_tools)
        result_event = None
        if not tool:
            logger.warning("No tool found for tool call: %s", tool_call.name)
            result_event = self._create_error_event(
                context,
                "tool_not_found",
                f"Tool '{tool_call.name}' not found",
                tool_call.id,
                tool_call.name,
            )
        else:
            try:
                result_event = await self._execute_tool(tool_call, tool, context)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error executing tool %s: %s", tool_call.name, ex, exc_info=True)
                result_event = self._create_error_event(
                    context,
                    "tool_execution_error",
                    str(ex),
                    tool_call.id,
                    tool_call.name,
                )
        function_response_events.append(result_event)
        return result_event

    async def execute_tools_async(
        self,
        tool_calls: List[FunctionCall],
        context: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Execute a list of tool calls and yield events for each result.

        This method:
        1. Uses resolved tools from process_llm_request
        2. Processes tool calls based on parallel_tool_calls setting:
           - If parallel_tool_calls=True: Executes all tools in parallel, merges results
           - If parallel_tool_calls=False: Executes tools sequentially, yields each immediately
        3. Finds appropriate tool for each call
        4. Executes the tool and collects/yields events
        5. Handles errors gracefully for individual tools

        Args:
            tool_calls: List of tool calls from the LLM
            context: The invocation context

        Yields:
            Event: Events representing tool execution results
                   - For parallel execution: Single merged event
                   - For sequential execution: Individual events as they complete
        """
        if not tool_calls:
            logger.debug("No tool calls to execute")
            return

        # Resolve tools for execution
        resolved_tools = await convert_toolunion_to_tool_list(self.tools, context)

        logger.debug("Starting execution of %s tool calls", len(tool_calls))

        # Split the batch by execution model. Progress-streaming tools are
        # **never** mixed into the legacy parallel/sequential path: they have
        # a different control flow (one tool call -> many events) that does
        # not compose with the "1 call -> 1 event, then merge" parallel
        # design. The non-streaming bucket is fed through the legacy path
        # *verbatim* so that we do not regress any existing behavior.
        streaming_calls, non_streaming_calls = self._split_calls_by_streaming(tool_calls, resolved_tools)

        # Capture state before tool execution
        state_begin = dict(context.session.state)

        # ---- Phase 1: legacy path for non-streaming tools (unchanged) ----
        if non_streaming_calls:
            parallel_tool_calls: bool = getattr(context.agent, "parallel_tool_calls", False)
            if parallel_tool_calls:
                # Parallel execution: collect all events and merge them
                function_response_events: list[Event] = []
                async with asyncio.TaskGroup() as tg:
                    for tool_call in non_streaming_calls:
                        tg.create_task(self.__invoke_tools(context, resolved_tools, tool_call,
                                                           function_response_events))

                # Handle merging and tracing based on number of events
                if function_response_events:
                    if len(function_response_events) == 1:
                        yield function_response_events[0]
                    else:
                        merged_event = self._merge_parallel_function_response_events(function_response_events)
                        state_end = dict(context.session.state)
                        if merged_event.actions and merged_event.actions.state_delta:
                            state_end.update(merged_event.actions.state_delta)
                        with tracer.start_as_current_span(
                                "execute_tool (merged)",
                                attributes={"gen_ai.operation.name": "execute_tool"},
                        ):
                            trace_merged_tool_calls(
                                response_event_id=merged_event.id,
                                function_response_event=merged_event,
                                state_begin=state_begin,
                                state_end=state_end,
                            )
                        yield merged_event
            else:
                # Sequential execution: yield each event immediately after execution
                for tool_call in non_streaming_calls:
                    function_response_events: list[Event] = []
                    result_event = await self.__invoke_tools(context, resolved_tools, tool_call,
                                                             function_response_events)
                    if result_event:
                        yield result_event

        # ---- Phase 2: uniform streaming path for progress-streaming tools ----
        # Streaming tools are always executed **sequentially among themselves**.
        # Interleaving their partials would force the consumer to demux events
        # by tool_call_id; we deliberately keep ordering deterministic instead.
        # See StreamingProgressTool docstring for the per-tool contract.
        for tool_call in streaming_calls:
            tool = await self._find_tool(tool_call, resolved_tools)
            if tool is None:
                yield self._create_error_event(
                    context,
                    "tool_not_found",
                    f"Tool '{tool_call.name}' not found",
                    tool_call.id,
                    tool_call.name,
                )
                continue
            async for ev in self._execute_progress_streaming_tool(tool_call, tool, context):
                yield ev

    @staticmethod
    def _split_calls_by_streaming(
        tool_calls: List[FunctionCall],
        resolved_tools: List[BaseTool],
    ) -> tuple[List[FunctionCall], List[FunctionCall]]:
        """Partition ``tool_calls`` into ``(streaming, non_streaming)`` lists.

        Calls whose target tool cannot be resolved (e.g. typo from the LLM)
        are placed in the **non_streaming** bucket so that the legacy path
        keeps producing the canonical ``tool_not_found`` error event.

        Relative order within each list is preserved so downstream tracing
        stays predictable.
        """
        by_name = {t.name: t for t in resolved_tools if isinstance(t, BaseTool)}
        streaming: List[FunctionCall] = []
        non_streaming: List[FunctionCall] = []
        for tc in tool_calls:
            tool = by_name.get(tc.name)
            if tool is not None and tool.is_progress_streaming:
                streaming.append(tc)
            else:
                non_streaming.append(tc)
        return streaming, non_streaming

    async def find_tool(self, context: InvocationContext, tool_call: FunctionCall) -> Optional[BaseTool]:
        """Find the appropriate tool for a tool call.

        This method first converts the tool union to a tool list, then finds
        the appropriate tool for the given tool call.

        Args:
            tool_call: The tool call to find a tool for
            context: The invocation context

        Returns:
            BaseTool: The tool that can handle the call, or None
        """
        # Convert tools first
        resolved_tools = await convert_toolunion_to_tool_list(self.tools, context)

        # Find the tool using the private method
        return await self._find_tool(tool_call, resolved_tools)

    async def _find_tool(self, tool_call: FunctionCall, resolved_tools: List[BaseTool]) -> Optional[BaseTool]:
        """Find the appropriate tool for a tool call.

        Args:
            tool_call: The tool call to find a tool for
            resolved_tools: List of resolved tools to search through

        Returns:
            BaseTool: The tool that can handle the call, or None
        """
        for tool in resolved_tools:
            if isinstance(tool, BaseTool):
                if tool.name == tool_call.name:
                    return tool
        return None

    async def _execute_tool(self, tool_call: FunctionCall, tool: BaseTool, context: InvocationContext) -> Event:
        """Execute a callable tool.

        Args:
            tool_call: The tool call to execute
            tool: The tool to execute
            context: The invocation context

        Returns:
            Event: The result of tool execution
        """

        # Wrap tool execution in telemetry span.
        # Pass initial attributes so the Galileo sampler can make a sampling
        # decision at span-creation time (before trace_tool_call sets them).
        with tracer.start_as_current_span(
                f"execute_tool {tool.name}",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": tool.name,
                    "gen_ai.tool.description": tool.description or "",
                },
        ):
            # Capture state before tool execution
            state_begin = dict(context.session.state)

            # Parse arguments (FunctionCall uses 'args' field)
            if isinstance(tool_call.args, str):
                arguments = json.loads(tool_call.args)
            else:
                arguments = tool_call.args or {}

            # Set function call ID for context
            context.function_call_id = tool_call.id

            start_time = time.monotonic()

            try:
                result = await tool.run_async(tool_context=context, args=arguments)
                execution_time = time.monotonic() - start_time

                report_execute_tool(
                    context,
                    tool,
                    duration_s=execution_time,
                    error_type=None,
                )

                # Build function response
                if not isinstance(result, dict):
                    function_result = {"result": result}
                else:
                    function_result = result

                # Create function response part
                part_function_response = Part.from_function_response(name=tool_call.name, response=function_result)
                part_function_response.function_response.id = tool_call.id

                # Create content with role='user'
                content = Content(
                    role="user",
                    parts=[part_function_response],
                )

                # Create event with proper content structure and state delta
                event = Event(
                    invocation_id=context.invocation_id,
                    author=context.agent.name,
                    content=content,
                    custom_metadata={"execution_time": execution_time} if execution_time is not None else {},
                    branch=context.branch,
                )

                # Capture state changes from tool execution
                if context.state.has_delta():
                    event.actions.state_delta.update(context.state._delta)

                # Capture any other actions set by the tool
                if context.event_actions.skip_summarization:
                    event.actions.skip_summarization = True
                if context.event_actions.transfer_to_agent:
                    event.actions.transfer_to_agent = context.event_actions.transfer_to_agent
                if context.event_actions.artifact_delta:
                    event.actions.artifact_delta.update(context.event_actions.artifact_delta)

                # Compute state after tool execution
                state_end = dict(context.session.state)
                if event.actions and event.actions.state_delta:
                    state_end.update(event.actions.state_delta)

                # Trace the tool call after building the function response event
                trace_tool_call(
                    tool=tool,
                    args=arguments,
                    function_response_event=event,
                    state_begin=state_begin,
                    state_end=state_end,
                )

                return event

            except Exception as ex:  # pylint: disable=broad-except
                report_execute_tool(
                    context,
                    tool,
                    duration_s=time.monotonic() - start_time,
                    error_type=type(ex).__name__,
                )

                error_event = self._create_error_event(context, "tool_execution_error", str(ex), tool_call.id,
                                                       tool_call.name)

                # Compute state after failed tool execution
                state_end = dict(context.session.state)

                # Trace the failed tool call
                trace_tool_call(
                    tool=tool,
                    args=arguments,
                    function_response_event=error_event,
                    state_begin=state_begin,
                    state_end=state_end,
                )

                return error_event

    async def _execute_progress_streaming_tool(
        self,
        tool_call: FunctionCall,
        tool: BaseTool,
        context: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Execute a progress-streaming tool, surfacing every yield as a partial event.

        Contract with :class:`StreamingProgressTool`:

        - Every value yielded by the tool's async generator becomes a
          ``partial=True`` Event with ``custom_metadata.tool_progress=True``.
          These events are *not* persisted into session history and are *not*
          fed back to the LLM as tool responses.
        - The **last** yielded value is additionally used to build the final
          function_response event (``partial=False``, with a real
          ``function_response`` Part) that closes this tool call.

        Args:
            tool_call: The LLM-issued FunctionCall to execute.
            tool: The resolved StreamingProgressTool instance.
            context: The invocation context.

        Yields:
            Event: zero or more partial progress events, followed by exactly
            one final function_response event (or an error event).
        """
        with tracer.start_as_current_span(
                f"execute_tool {tool.name} (streaming)",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": tool.name,
                    "gen_ai.tool.description": tool.description or "",
                },
        ):
            state_begin = dict(context.session.state)

            if isinstance(tool_call.args, str):
                arguments = json.loads(tool_call.args)
            else:
                arguments = tool_call.args or {}

            context.function_call_id = tool_call.id
            start_time = time.monotonic()

            run_streaming = getattr(tool, "run_streaming", None)
            if run_streaming is None:
                # Defensive: a tool advertising is_progress_streaming=True
                # without run_streaming() is broken. Fall back to non-streaming.
                logger.warning(
                    "Tool %s sets is_progress_streaming=True but exposes no run_streaming(); "
                    "falling back to non-streaming execution.",
                    tool.name,
                )
                final_event = await self._execute_tool(tool_call, tool, context)
                yield final_event
                return

            last_value: Any = None
            progress_count = 0
            skip_summarization = bool(getattr(tool, "skip_summarization", False))

            try:
                # Drain the streaming generator. Buffer the previously-seen
                # value and emit it as a partial event only when a *next*
                # value arrives, so that the last value is reserved for the
                # final function_response event.
                async for value in run_streaming(tool_context=context, args=arguments):
                    if last_value is not None:
                        yield self._build_progress_event(context, tool_call, tool, last_value)
                        progress_count += 1
                    last_value = value

                execution_time = time.monotonic() - start_time
                report_execute_tool(
                    context,
                    tool,
                    duration_s=execution_time,
                    error_type=None,
                )

                final_result = last_value if last_value is not None else {}
                if not isinstance(final_result, dict):
                    final_result = {"result": final_result}

                part_function_response = Part.from_function_response(name=tool_call.name, response=final_result)
                part_function_response.function_response.id = tool_call.id

                final_event = Event(
                    invocation_id=context.invocation_id,
                    author=context.agent.name,
                    content=Content(role="user", parts=[part_function_response]),
                    custom_metadata={
                        "execution_time": execution_time,
                        "progress_events": progress_count,
                    },
                    branch=context.branch,
                )

                if context.state.has_delta():
                    final_event.actions.state_delta.update(context.state._delta)  # pylint: disable=protected-access
                if context.event_actions.skip_summarization or skip_summarization:
                    # Either the tool declared the streamed output as the final
                    # answer at construction time, or it asked for it via the
                    # event_actions context bag during execution.
                    final_event.actions.skip_summarization = True
                if context.event_actions.transfer_to_agent:
                    final_event.actions.transfer_to_agent = context.event_actions.transfer_to_agent
                if context.event_actions.artifact_delta:
                    final_event.actions.artifact_delta.update(context.event_actions.artifact_delta)

                state_end = dict(context.session.state)
                if final_event.actions and final_event.actions.state_delta:
                    state_end.update(final_event.actions.state_delta)

                trace_tool_call(
                    tool=tool,
                    args=arguments,
                    function_response_event=final_event,
                    state_begin=state_begin,
                    state_end=state_end,
                )

                yield final_event

            except Exception as ex:  # pylint: disable=broad-except
                report_execute_tool(
                    context,
                    tool,
                    duration_s=time.monotonic() - start_time,
                    error_type=type(ex).__name__,
                )
                error_event = self._create_error_event(
                    context,
                    "tool_execution_error",
                    str(ex),
                    tool_call.id,
                    tool_call.name,
                )
                state_end = dict(context.session.state)
                trace_tool_call(
                    tool=tool,
                    args=arguments,
                    function_response_event=error_event,
                    state_begin=state_begin,
                    state_end=state_end,
                )
                logger.error("Error executing streaming tool %s: %s", tool_call.name, ex, exc_info=True)
                yield error_event

    @staticmethod
    def _build_progress_event(
        context: InvocationContext,
        tool_call: FunctionCall,
        tool: BaseTool,
        value: Any,
    ) -> Event:
        """Wrap a single value yielded by a streaming tool into a partial Event.

        Rules:
        - ``str`` → rendered as a text Part directly.
        - ``dict`` / anything else → rendered as JSON text Part; the raw
          value is also attached under ``custom_metadata['payload']`` so
          structured consumers can read it without re-parsing.
        - The event is marked ``partial=True`` so session services skip
          persisting it and the LLM never sees it as a tool response.
        - ``custom_metadata`` carries ``tool_progress=True``, ``tool_name``,
          ``tool_call_id`` to make filtering on the consumer side trivial.
        """
        if isinstance(value, str):
            text = value
            payload: Optional[Any] = None
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(value)
            payload = value

        custom_metadata = {
            "tool_progress": True,
            "tool_name": tool.name,
            "tool_call_id": tool_call.id,
        }
        if payload is not None:
            custom_metadata["payload"] = payload

        return Event(
            invocation_id=context.invocation_id,
            author=context.agent.name,
            content=Content(role="model", parts=[Part(text=text)]),
            partial=True,
            branch=context.branch,
            custom_metadata=custom_metadata,
        )

    def _merge_parallel_function_response_events(self, function_response_events: List[Event]) -> Event:
        """Merge multiple function response events into a single event.

        This follows the TrpcAgent pattern for merging parallel tool execution results.

        Args:
            function_response_events: List of individual tool response events

        Returns:
            Event: Merged event containing all tool responses
        """
        if not function_response_events:
            raise ValueError("No function response events provided.")

        if len(function_response_events) == 1:
            return function_response_events[0]

        # Collect all parts from all events
        merged_parts = []
        for event in function_response_events:
            if event.content and event.content.parts:
                merged_parts.extend(event.content.parts)

        # Use the first event as the "base" for common attributes
        base_event = function_response_events[0]

        # Merge actions from all events
        merged_actions = EventActions()
        for event in function_response_events:
            if event.actions.skip_summarization:
                merged_actions.skip_summarization = True
            if event.actions.transfer_to_agent:
                merged_actions.transfer_to_agent = event.actions.transfer_to_agent
            if event.actions.state_delta:
                merged_actions.state_delta.update(event.actions.state_delta)
            if event.actions.artifact_delta:
                merged_actions.artifact_delta.update(event.actions.artifact_delta)

        # Create the new merged event
        merged_event = Event(
            invocation_id=Event.new_id(),
            author=base_event.author,
            content=Content(role="user", parts=merged_parts),
            actions=merged_actions,
            branch=base_event.branch,
        )

        # Use the base_event timestamp
        merged_event.timestamp = base_event.timestamp
        return merged_event

    def _create_error_event(
        self,
        ctx: InvocationContext,
        error_code: str,
        error_message: str,
        tool_call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> Event:
        """Create an error event with proper function_response structure.

        This method creates an error event that can be properly converted to OpenAI
        tool message format, ensuring error messages are correctly passed to the LLM.

        Args:
            ctx: The invocation context containing agent information
            error_code: The error code for the event
            error_message: The error message for the event
            tool_call_id: The ID of the failed tool call (optional)
            tool_name: The name of the failed tool (optional)

        Returns:
            Event: Error event with proper function_response structure
        """
        # Create error response content
        error_response = {
            "error": error_code,
            "message": error_message,
            "status": "failed",
        }

        # Create function response part
        part_function_response = Part.from_function_response(
            name=tool_name or "unknown_tool",
            response=error_response,
        )

        # Set tool_call_id if provided

        part_function_response.function_response.id = tool_call_id or "unknown_tool_call_id"

        # Create content with role='user'
        content = Content(
            role="user",
            parts=[part_function_response],
        )

        # Create event with proper content structure
        return Event(
            invocation_id=ctx.invocation_id,
            author=ctx.agent.name,
            content=content,
            error_code=error_code,
            error_message=error_message,
            branch=ctx.branch,
        )
