# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools Processor implementation for TRPC Agent framework.

This module provides the ToolsProcessor class which handles tool invocation
and processing for agents. It uses the unified Event system for communication.

The ToolsProcessor is simplified to directly use the unified Event class.
"""

from __future__ import annotations

import asyncio
import json
import time
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

        # Capture state before tool execution
        state_begin = dict(context.session.state)

        parallel_tool_calls: bool = getattr(context.agent, "parallel_tool_calls", False)

        if parallel_tool_calls:
            # Parallel execution: collect all events and merge them
            function_response_events: list[Event] = []
            async with asyncio.TaskGroup() as tg:
                for tool_call in tool_calls:
                    tg.create_task(self.__invoke_tools(context, resolved_tools, tool_call, function_response_events))

            # Handle merging and tracing based on number of events
            if not function_response_events:
                return

            if len(function_response_events) == 1:
                # Single tool call - yield the event directly
                yield function_response_events[0]
            else:
                # Multiple tool calls - merge them and add merged tracing
                merged_event = self._merge_parallel_function_response_events(function_response_events)

                # Compute state after merged tool execution
                state_end = dict(context.session.state)
                if merged_event.actions and merged_event.actions.state_delta:
                    state_end.update(merged_event.actions.state_delta)

                # Add merged tool call tracing
                with tracer.start_as_current_span("execute_tool (merged)"):
                    trace_merged_tool_calls(
                        response_event_id=merged_event.id,
                        function_response_event=merged_event,
                        state_begin=state_begin,
                        state_end=state_end,
                    )

                yield merged_event
        else:
            # Sequential execution: yield each event immediately after execution
            for tool_call in tool_calls:
                function_response_events: list[Event] = []
                result_event = await self.__invoke_tools(context, resolved_tools, tool_call, function_response_events)
                if result_event:
                    yield result_event

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

        # Wrap tool execution in telemetry span
        with tracer.start_as_current_span(f"execute_tool {tool.name}"):
            # Capture state before tool execution
            state_begin = dict(context.session.state)

            # Parse arguments (FunctionCall uses 'args' field)
            if isinstance(tool_call.args, str):
                arguments = json.loads(tool_call.args)
            else:
                arguments = tool_call.args or {}

            # Set function call ID for context
            context.function_call_id = tool_call.id

            try:
                start_time = time.time()
                result = await tool.run_async(tool_context=context, args=arguments)
                execution_time = time.time() - start_time

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
