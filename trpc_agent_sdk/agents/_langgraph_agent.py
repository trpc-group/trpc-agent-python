# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""LangGraph Agent for TRPC framework.

This agent integrates LangGraph's streaming capabilities with the TRPC Agent framework,
supporting all stream modes: values, updates, custom, messages, and debug.
"""

from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import Optional
from typing import Union
from typing_extensions import override

from google.genai import types
from langchain_core.messages import AIMessage
from langchain_core.messages import AIMessageChunk
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langchain_core.messages import ToolMessage
from langchain_core.messages import convert_to_messages
from langchain_core.messages.tool import ToolCall
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel
from pydantic import ConfigDict

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse

from ..exceptions import RunCancelledException
from ._base_agent import BaseAgent
from .utils import AGENT_CTX_KEY
from .utils import CHUNK_KEY
from .utils import LANGGRAPH_KEY
from .utils import STREAM_MODE_KEY
from .utils import TRPC_AGENT_KEY
from .utils import extract_trpc_event
from .utils import is_trpc_event_chunk

# LangGraph interrupt constant
_INTERRUPT_KEY: str = "__interrupt__"

# TRPC long running prefix constant
_TRPC_LONG_RUNNING_PREFIX: str = "__trpc_long_running__"


class LangGraphAgent(BaseAgent):
    """LangGraph Agent with streaming support for TRPC framework.

    This agent integrates LangGraph's streaming capabilities with the TRPC Agent framework,
    supporting all stream modes: values, updates, custom, messages, and debug.

    Configuration via RunConfig:
        - stream_mode: List of stream modes to enable (extends default: updates, custom, messages)
        - runnable_config: LangChain RunnableConfig for the graph execution
        - input: Custom input dictionary to merge with the default {"messages": []} input
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, )
    """The pydantic model config."""

    graph: CompiledStateGraph
    """The compiled LangGraph state graph."""

    instruction: str = ""
    """Instructions for the agent."""

    output_key: Optional[str] = None
    """Key in session state to store agent output for later use."""

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:

        # CHECKPOINT 1: At method entry
        await ctx.raise_if_cancelled()

        # Parse agent configuration
        config = self._parse_agent_config(ctx)
        stream_modes = config[STREAM_MODE_KEY]
        runnable_config = config["runnable_config"]
        custom_input = config["input"]
        subgraphs = config.get("subgraphs", False)

        # Check if we have a resume command from events
        resume_command = self._extract_resume_command(ctx.session.events)

        if not resume_command:
            # Build messages when no resume command is present
            messages = self._build_messages(ctx, runnable_config)
            logger.debug("messages: %s", messages)
            # Prepare input for astream - merge messages with custom input
            astream_input = {"messages": messages}
            if custom_input:
                # Merge custom input with default messages input
                astream_input.update(custom_input)
                logger.debug("Custom input provided: %s", custom_input)
        else:
            # Use the Command directly for astream
            astream_input = resume_command
            logger.debug("Using resume command: %s", resume_command)

        try:
            async for chunk in self.graph.astream(astream_input,
                                                  runnable_config,
                                                  stream_mode=stream_modes,
                                                  subgraphs=subgraphs):
                # Handle subgraphs output format:
                # - subgraphs=True + multiple stream_modes: (namespace, stream_mode, data) - 3-tuple
                # - subgraphs=False + multiple stream_modes: (stream_mode, data) - 2-tuple
                if subgraphs:
                    namespace, stream_mode, chunk_data = chunk
                    logger.debug("subgraph namespace: %s, stream_mode: %s, chunk: %s", namespace, stream_mode,
                                 chunk_data)
                else:
                    stream_mode, chunk_data = chunk
                    namespace = ()
                    logger.debug("stream_mode: %s, chunk: %s", stream_mode, chunk_data)

                # Check for interrupt in chunk
                if self._check_for_interrupt_in_chunk(chunk_data):
                    interrupt_data = chunk_data[_INTERRUPT_KEY]
                    for interrupt in interrupt_data:
                        await ctx.raise_if_cancelled()

                        # Create all interrupt-related events
                        logger.debug("get interrupt: %s", interrupt)
                        fc_call_event, fc_rsp_event, long_running_event = self._create_interrupt_events(
                            ctx, interrupt, stream_mode, chunk_data)
                        logger.debug("getting LongRunningEvent: %s", long_running_event)

                        # Yield the events in order
                        yield fc_call_event
                        yield fc_rsp_event
                        yield long_running_event
                        continue

                if stream_mode == "messages":
                    # Handle LLM token streaming - chunk is (token, metadata)
                    token, _ = chunk_data
                    # Only yield AIMessageChunk. Other type messages should be raised by updates
                    if not isinstance(token, AIMessageChunk):
                        continue
                    event = Event(
                        invocation_id=ctx.invocation_id,
                        author=self.name,
                        branch=ctx.branch,
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=token.content)],
                        ),
                        custom_metadata=self._build_custom_metadata(stream_mode, chunk_data),
                        partial=True,
                    )

                    yield event
                elif stream_mode == "updates":
                    # Handle complete message updates - these are partial=False
                    # When subgraphs=True and namespace is empty, this is a parent graph node update
                    # The parent node's messages contain all accumulated messages from subgraph,
                    # which have already been emitted by subgraph internal nodes.
                    # Skip emitting messages from parent node to avoid duplicates.
                    if subgraphs and not namespace:
                        # Parent graph node update - only emit custom_metadata, skip messages
                        logger.debug("Skipping messages from parent node to avoid duplicates")
                        event = Event(
                            invocation_id=ctx.invocation_id,
                            author=self.name,
                            branch=ctx.branch,
                            custom_metadata=self._build_custom_metadata(stream_mode, chunk_data),
                            partial=True,
                        )
                        yield event
                        continue
                    for _, node_data in chunk_data.items():
                        # Skip if node_data when node returns empty dict or None
                        if not node_data:
                            continue
                        if "messages" in node_data:
                            for message in node_data["messages"]:
                                event = self._build_event_from_message(ctx, message, stream_mode, chunk_data)
                                if event:
                                    # Save output to state if this is a final response
                                    if event.is_final_response():
                                        self._save_output_to_state(ctx, event)
                                    yield event
                else:
                    # Handle other stream modes (custom, debug)
                    # NEW: Check if this is a trpc Event from a node using LangGraphEventWriter
                    if is_trpc_event_chunk(chunk_data):
                        trpc_event = extract_trpc_event(chunk_data)
                        yield trpc_event
                    else:
                        # Existing behavior: wrap in custom_metadata
                        event = Event(
                            invocation_id=ctx.invocation_id,
                            author=self.name,
                            branch=ctx.branch,
                            custom_metadata=self._build_custom_metadata(stream_mode, chunk_data),
                            partial=True,
                        )
                        yield event

                # CHECKPOINT 2: At each chunk iteration
                await ctx.raise_if_cancelled()
        except RunCancelledException:
            # Re-raise to let Runner handle cleanup
            raise

    def _apply_template_substitution(self, instruction: str, ctx: InvocationContext) -> str:
        """Apply template substitution to replace {key} placeholders with state values.

        This method replaces template placeholders like {user:theme}, {app:config},
        {session_key}, etc. with actual values from the session state.

        This implementation matches the one in _request_processor.py to provide
        consistent template substitution behavior across agent types.

        Args:
            instruction: The instruction string containing template placeholders
            ctx: The invocation context with session state

        Returns:
            str: The instruction with template placeholders replaced with actual values
        """
        if not instruction or "{" not in instruction:
            return instruction

        # Get all state values from the session
        state_dict = ctx.session.state if ctx.session else {}

        try:
            # Use regex to find and replace placeholders one by one
            import re

            def replace_placeholder(match):
                """Replace a single placeholder with its value."""
                var_name = match.group().lstrip("{").rstrip("}").strip()
                optional = False

                # Handle optional variables (ending with ?)
                if var_name.endswith("?"):
                    optional = True
                    var_name = var_name.removesuffix("?")

                # Check if the variable exists in state
                if var_name in state_dict:
                    # Convert the value to string safely
                    value = state_dict[var_name]
                    return str(value) if value is not None else ""
                else:
                    if optional:
                        # Optional variable not found - return empty string
                        return ""
                    else:
                        # Required variable not found - leave placeholder unchanged
                        # This follows the behavior of the original SafeFormatter approach
                        return match.group()

            # Use regex pattern similar to adk-python but simpler for trpc_agent_sdk
            # This matches {variable_name} patterns including optional ones with ?
            pattern = r"\{[^{}]*\}"
            result = re.sub(pattern, replace_placeholder, instruction)

            logger.debug("Template substitution completed. Original: %s..., Result: %s...", instruction[:100],
                         result[:100])
            return result
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Template substitution failed for instruction: %s", ex)
            # Return original instruction if formatting fails
            return instruction

    def _save_output_to_state(self, ctx: InvocationContext, event: Event) -> None:
        """Save agent output to session state if output_key is configured.

        Args:
            ctx: The invocation context
            event: The event containing the content to save
        """
        if self.output_key and event.content and event.content.parts:
            # Save output to state using the delta tracking system
            result = "".join([part.text for part in event.content.parts if part.text])
            if result:  # Only save non-empty results
                ctx.state[self.output_key] = result
                event.actions.state_delta[self.output_key] = result
                logger.debug("Saved agent output to state key '%s': %s...", self.output_key, result[:100])

    def _build_custom_metadata(self, stream_mode: str, chunk: Any) -> Dict[str, Any]:
        """Build custom metadata structure for LangGraph events.

        Args:
            stream_mode: The LangGraph stream mode
            chunk: The chunk data from LangGraph

        Returns:
            Dictionary with nested structure containing stream mode and chunk
        """
        # Serialize Message objects in 'updates' mode to avoid JSON serialization errors
        if stream_mode == "updates":
            for node_output in chunk.values():
                if isinstance(node_output, dict) and "messages" in node_output:
                    # Convert LangChain Message objects to dictionaries for JSON compatibility
                    message_objects = node_output.pop("messages", [])
                    messages_res = []
                    for msg in message_objects:
                        if isinstance(msg, BaseModel):
                            messages_res.append(msg.model_dump_json())
                        else:
                            messages_res.append(msg)
                    node_output["messages"] = messages_res

        return {LANGGRAPH_KEY: {STREAM_MODE_KEY: stream_mode, CHUNK_KEY: chunk}}

    def _check_for_interrupt_in_chunk(self, chunk: Any) -> bool:
        """Check if a chunk contains interrupt data.

        Args:
            chunk: The chunk data from LangGraph streaming

        Returns:
            True if the chunk contains interrupt data, False otherwise
        """
        # Check if chunk has __interrupt__ key with tuple containing Interrupt object
        if isinstance(chunk, dict) and _INTERRUPT_KEY in chunk:
            interrupt_data = chunk[_INTERRUPT_KEY]
            # Check if it's a tuple containing an Interrupt object
            if isinstance(interrupt_data, tuple) and len(interrupt_data) > 0:
                return True
        return False

    def _create_interrupt_events(self, ctx: InvocationContext, interrupt, stream_mode: str,
                                 chunk: Any) -> tuple[Event, Event, LongRunningEvent]:
        """Create function call, function response, and long running events for an interrupt.

        Args:
            ctx: The invocation context
            interrupt: The LangGraph interrupt object
            stream_mode: The current stream mode
            chunk: The chunk data from LangGraph

        Returns:
            Tuple of (function_call_event, function_response_event, long_running_event)
        """
        # Extract ID and function name from interrupt namespace
        id = interrupt.ns[0]
        func_name = id.split(":")[0]

        # Create a synthetic function call for the interrupt
        function_call = FunctionCall(
            id=f"{_TRPC_LONG_RUNNING_PREFIX}{id}",
            name=func_name,
            args=interrupt.value,
        )

        # Create Event for function call
        function_call_event = Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="model",
                parts=[types.Part(function_call=function_call)],
            ),
            custom_metadata=self._build_custom_metadata(stream_mode, chunk),
            partial=False,
        )

        function_response = FunctionResponse(
            id=function_call.id,
            name=function_call.name,
            response=interrupt.value,
        )

        # Create Event for function response
        function_response_event = Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="user",
                parts=[types.Part(function_response=function_response)],
            ),
            custom_metadata=self._build_custom_metadata(stream_mode, chunk),
            partial=False,
        )

        # Create LongRunningEvent
        long_running_event = LongRunningEvent(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            function_call=function_call,
            function_response=function_response,
        )

        return function_call_event, function_response_event, long_running_event

    def _extract_resume_command(self, events: list[Event]) -> Optional[Command]:
        """Extract resume command from events if present.

        Looks for function responses with TRPC long running prefix and converts
        them to LangGraph resume commands.

        Args:
            events: List of events to search for resume commands

        Returns:
            Resume Command if found, None otherwise
        """
        # Must use checkpointer to resume
        if not self.graph.checkpointer or len(events) == 0:
            return None
        last_event = events[-1]
        if last_event.author == "user" and last_event.content and last_event.content.parts:
            part = last_event.content.parts[0]
            fc_rsp = part.function_response
            if fc_rsp and fc_rsp.id and fc_rsp.id.startswith(_TRPC_LONG_RUNNING_PREFIX):
                return Command(resume=fc_rsp.response)
        return None

    def _build_messages(self, ctx: InvocationContext, runnable_config: RunnableConfig) -> list:
        """Build messages for LangGraph when no resume command is present.

        If ctx.override_messages is set (e.g., by TeamAgent for member control),
        those messages are used directly instead of building from session events.

        Args:
            ctx: The agent context
            runnable_config: The runnable configuration

        Returns:
            List of messages to send to LangGraph
        """
        messages = []

        # Add instruction as SystemMessage if graph state is empty, with template substitution
        if self.graph.checkpointer:
            current_graph_state = self.graph.get_state(runnable_config)
            graph_messages = current_graph_state.values.get("messages", []) if current_graph_state.values else []
            if self.instruction and not graph_messages:
                # Apply template substitution to instruction
                processed_instruction = self._apply_template_substitution(self.instruction, ctx)
                messages = [SystemMessage(content=processed_instruction)]
            else:
                messages = []
        else:
            if self.instruction:
                # Apply template substitution to instruction
                processed_instruction = self._apply_template_substitution(self.instruction, ctx)
                messages = [SystemMessage(content=processed_instruction)]
            else:
                messages = []

        # Check if override_messages is provided (e.g., by TeamAgent)
        if ctx.override_messages is not None:
            # Convert override_messages (Content objects) to LangChain messages
            messages += self._convert_override_messages_to_langchain(ctx.override_messages)
            print(f"LangGraph agent {self.name} using override_messages: {ctx.override_messages}")
            logger.debug("Used %s override messages for LangGraph agent: %s", len(ctx.override_messages), self.name)
        else:
            # Add events to messages (evaluating the memory used; parent agent vs checkpointer)
            messages += self._get_messages(ctx.session.events)
        return messages

    def _convert_override_messages_to_langchain(self, override_messages: list) -> list:
        """Convert override_messages (Content objects) to LangChain messages.

        Args:
            override_messages: List of Content objects from TeamAgent

        Returns:
            List of LangChain messages (HumanMessage, AIMessage, ToolMessage)
        """
        from trpc_agent_sdk.types import Content
        langchain_messages = []

        for content in override_messages:
            if not isinstance(content, Content) or not content.parts:
                continue

            role = content.role or "user"

            for part in content.parts:
                if part.text:
                    if role == "user":
                        langchain_messages.append(HumanMessage(content=part.text))
                    else:
                        langchain_messages.append(AIMessage(content=part.text))
                elif part.function_call:
                    # Convert to AIMessage with tool_calls
                    tool_call = ToolCall(
                        name=part.function_call.name,
                        args=part.function_call.args or {},
                        id=part.function_call.id or f"call_{hash(part.function_call.name)}",
                    )
                    langchain_messages.append(AIMessage(content="", tool_calls=[tool_call]))
                elif part.function_response:
                    # Convert to ToolMessage
                    response_content = part.function_response.response
                    if isinstance(response_content, dict):
                        content_str = response_content.get("result", str(response_content))
                    else:
                        content_str = str(response_content) if response_content else ""

                    tool_call_id = part.function_response.id or f"call_{hash(part.function_response.name)}"
                    langchain_messages.append(
                        ToolMessage(content=content_str, name=part.function_response.name, tool_call_id=tool_call_id))

        return langchain_messages

    def _parse_agent_config(self, ctx: InvocationContext) -> Dict[str, Any]:
        """Parse agent configuration and return stream mode, runnable config, and input.

        Args:
            ctx: The invocation context

        Returns:
            Dictionary containing 'stream_mode', 'runnable_config', 'input', and 'subgraphs' keys
        """
        # Configure stream modes (can be overridden by agent_run_config)
        if STREAM_MODE_KEY in ctx.run_config.agent_run_config:
            # If stream_mode is explicitly configured, use it (override default)
            stream_mode = list(ctx.run_config.agent_run_config[STREAM_MODE_KEY])
        else:
            # Default stream modes
            stream_mode = [
                "updates",
                "custom",
                "messages",
            ]

        # Configure runnable config
        if "runnable_config" in ctx.run_config.agent_run_config:
            runnable_config = ctx.run_config.agent_run_config["runnable_config"]
        else:
            runnable_config: RunnableConfig = {"configurable": {"thread_id": ctx.session.id}}
        runnable_config[TRPC_AGENT_KEY] = {AGENT_CTX_KEY: ctx}

        # Extract custom input from agent_run_config
        custom_input = ctx.run_config.agent_run_config.get("input", {})

        # Extract subgraphs config (default False for backward compatibility)
        subgraphs = ctx.run_config.agent_run_config.get("subgraphs", False)

        return {
            STREAM_MODE_KEY: stream_mode,
            "runnable_config": runnable_config,
            "input": custom_input,
            "subgraphs": subgraphs
        }

    def _build_event_from_message(self, ctx: InvocationContext, message, stream_mode: str,
                                  chunk: Any) -> Optional[Event]:
        """Build an Event from a LangChain message.

        Args:
            ctx: The invocation context
            message: The LangChain message (AIMessage or ToolMessage)
            stream_mode: The stream mode
            chunk: The chunk data

        Returns:
            Event or None if message type is not supported
        """
        if isinstance(message, AIMessage):
            # Handle AIMessage - could be regular text or tool calls
            parts = []
            usage_metadata = None

            # Extract usage metadata if available
            if hasattr(message, "usage_metadata") and message.usage_metadata:
                usage_data = message.usage_metadata
                usage_metadata = types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=usage_data.get("input_tokens", 0),
                    candidates_token_count=usage_data.get("output_tokens", 0),
                    total_token_count=usage_data.get("total_tokens", 0),
                )

            # Check if this is a tool call message
            if hasattr(message, "tool_calls") and message.tool_calls:
                # Build function call parts for each tool invocation
                tool_call_parts = []
                for tool_call in message.tool_calls:
                    # Create FunctionCall with all parameters in constructor for reliability
                    function_call = types.FunctionCall(
                        id=tool_call["id"],
                        name=tool_call["name"],
                        args=tool_call["args"],
                    )
                    tool_call_parts.append(types.Part(function_call=function_call))
                parts.extend(tool_call_parts)
            elif message.content:
                # This is a regular text message
                parts.append(types.Part.from_text(text=message.content))

            if parts:
                event = Event(
                    invocation_id=ctx.invocation_id,
                    author=self.name,
                    branch=ctx.branch,
                    content=types.Content(
                        role="model",
                        parts=parts,
                    ),
                    usage_metadata=usage_metadata,
                    custom_metadata=self._build_custom_metadata(stream_mode, chunk),
                    partial=False,  # Complete message
                )

                return event

        elif isinstance(message, ToolMessage):
            # Handle ToolMessage - function response
            # Try to parse content as structured data, fallback to simple result wrapper
            try:
                import json

                # Attempt to parse JSON string content
                parsed_content = json.loads(message.content) if isinstance(message.content, str) else message.content

                # Ensure response data is a dictionary (wrap non-dict types)
                if isinstance(parsed_content, dict):
                    response_data = parsed_content
                else:
                    response_data = {"result": parsed_content}

            except (json.JSONDecodeError, TypeError):
                # Fallback: wrap unparsable content in result field
                response_data = {"result": message.content}

            # Create FunctionResponse with all parameters in constructor
            function_response = types.FunctionResponse(
                id=message.tool_call_id,
                name=message.name,
                response=response_data,
            )
            parts = [types.Part(function_response=function_response)]

            event = Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=types.Content(
                    role="model",
                    parts=parts,
                ),
                custom_metadata=self._build_custom_metadata(stream_mode, chunk),
                partial=False,  # Complete message
            )

            return event

        return None

    def _get_last_human_messages(self, events: list[Event]) -> list[HumanMessage]:
        """Extracts last human messages from given list of events.

        Only processes text messages. Function responses with TRPC long running prefix
        are handled separately by _extract_resume_command.

        Args:
          events: the list of events

        Returns:
          list of last human messages
        """
        messages = []
        for event in reversed(events):
            if messages and event.author != "user":
                break
            if event.author == "user" and event.content and event.content.parts:
                part = event.content.parts[0]
                if part.text:
                    # Regular text message
                    messages.append(HumanMessage(content=part.text))
                    break
                else:
                    raise ValueError(f"Invalid message part: {part}")
        return list(messages)

    def _get_messages(self, events: list[Event]) -> list[Union[HumanMessage, AIMessage, ToolMessage]]:
        """Extracts messages from given list of events.

        If the developer provides their own memory within langgraph, we return the
        last user messages only. Otherwise, we return all messages between the user
        and the agent.

        Args:
          events: the list of events

        Returns:
          list of messages
        """
        if self.graph.checkpointer:
            return self._get_last_human_messages(events)
        else:
            return self._get_conversation_with_agent(events)

    def _get_conversation_with_agent(self, events: list[Event]) -> list[Union[HumanMessage, AIMessage, ToolMessage]]:
        """Extracts messages from given list of events.

        Args:
          events: the list of events

        Returns:
          list of messages
        """

        messages = []
        for event in events:
            if not event.content or not event.content.parts:
                continue

            if event.author == "user":
                # User messages are always text
                if event.content.parts[0].text:
                    messages.append(HumanMessage(content=event.content.parts[0].text))
            elif event.author == self.name:
                # Agent messages can be text, function calls, or function responses
                converted_messages = self._convert_event_to_message(event)
                if converted_messages:
                    messages.extend(converted_messages)
        return messages

    def _convert_event_to_message(self, event: Event) -> list[Union[AIMessage, ToolMessage]]:
        """Convert an Event back to LangChain messages.

        Args:
            event: The Event to convert

        Returns:
            List of LangChain messages (AIMessage for text/function calls, ToolMessage for function responses)
        """
        if not event.content or not event.content.parts:
            return []

        # First try to extract from LangGraph metadata
        langgraph_messages = self._extract_messages_from_langgraph_metadata(event)
        if langgraph_messages:
            return langgraph_messages

        # Fallback to converting from parts
        return self._convert_parts_to_messages(event)

    def _extract_messages_from_langgraph_metadata(self, event: Event) -> list[Union[AIMessage, ToolMessage]]:
        """Extract and clean messages from LangGraph custom metadata.

        Args:
            event: The Event with potential LangGraph metadata

        Returns:
            List of cleaned LangChain messages, or empty list if no metadata found
        """
        if not (event.custom_metadata and LANGGRAPH_KEY in event.custom_metadata):
            return []

        # Extract chunk data from metadata
        langgraph_metadata = event.custom_metadata[LANGGRAPH_KEY]
        chunk_data = langgraph_metadata[CHUNK_KEY]

        # Search for messages in any node output
        for node_output in chunk_data.values():
            if isinstance(node_output, dict) and "messages" in node_output:
                # Convert serialized message dicts back to Message objects
                serialized_messages = node_output["messages"]
                return convert_to_messages(serialized_messages)

        return []

    def _convert_parts_to_messages(self, event: Event) -> list[Union[AIMessage, ToolMessage]]:
        """Convert event parts to LangChain messages.

        Args:
            event: The Event to convert

        Returns:
            List of LangChain messages
        """
        messages = []

        for part in event.content.parts:
            if part.function_call:
                logger.debug("build function_call message with custom_metadata: %s", event.custom_metadata)
                function_call = ToolCall(
                    name=part.function_call.name,
                    args=part.function_call.args,
                    id=part.function_call.id,
                )
                ai_message = AIMessage(content="", tool_calls=[function_call])
                messages.append(ai_message)

            elif part.function_response:
                logger.debug("build function_response message with custom_metadata: %s", event.custom_metadata)
                response_content = part.function_response.response
                if isinstance(response_content, dict):
                    content = response_content.get("result", str(response_content))
                else:
                    content = str(response_content)

                tool_call_id = part.function_response.id
                if not tool_call_id:
                    tool_call_id = f"unknown_{hash(part.function_response.name)}"

                tool_message = ToolMessage(content=content, name=part.function_response.name, tool_call_id=tool_call_id)
                messages.append(tool_message)

            elif part.text:
                ai_message = AIMessage(content=part.text)
                messages.append(ai_message)

        return messages
