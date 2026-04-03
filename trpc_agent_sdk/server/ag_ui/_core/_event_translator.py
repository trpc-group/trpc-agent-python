# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""Event translator for converting TRPC agent events to AG-UI protocol events."""

import json
import uuid
from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import List
from typing import Optional

from ag_ui.core import BaseEvent
from ag_ui.core import CustomEvent
from ag_ui.core import EventType
from ag_ui.core import RunErrorEvent
from ag_ui.core import StateDeltaEvent
from ag_ui.core import StateSnapshotEvent
from ag_ui.core import TextMessageContentEvent
from ag_ui.core import TextMessageEndEvent
from ag_ui.core import TextMessageStartEvent
from ag_ui.core import ThinkingEndEvent
from ag_ui.core import ThinkingStartEvent
from ag_ui.core import ThinkingTextMessageContentEvent
from ag_ui.core import ThinkingTextMessageEndEvent
from ag_ui.core import ThinkingTextMessageStartEvent
from ag_ui.core import ToolCallArgsEvent
from ag_ui.core import ToolCallEndEvent
from ag_ui.core import ToolCallResultEvent
from ag_ui.core import ToolCallStartEvent
from trpc_agent_sdk import types
from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.events import Event as TRPCEvent
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS


class EventTranslator:
    """Translates TRPC agent events to AG-UI protocol events.

    This class handles the conversion between the two event systems,
    managing streaming sequences and maintaining event consistency.
    """

    def __init__(self, long_running_tool_names: Optional[List[str]] = None):
        """Initialize the event translator.

        Args:
            long_running_tool_names: List of long-running tool names to track
        """
        # Track tool call IDs for consistency
        self._active_tool_calls: Dict[str, str] = {}  # Tool call ID -> Tool call ID (for consistency)
        # Track streaming message state
        self._streaming_message_id: Optional[str] = None  # Current streaming message ID
        self._is_streaming: bool = False  # Whether we're currently streaming a message
        self._text_was_streamed: bool = False  # Whether text was already delivered via streaming (survives force_close)
        # Track thinking message state
        self._is_thinking: bool = False  # Whether we're currently streaming a thinking message
        self._thinking_text: str = ""
        self.long_running_tool_names: List[str] = long_running_tool_names or []  # Track the long running tool names
        # Track streaming tool calls - tool_call_id -> last_args_length
        self._streaming_tool_calls: Dict[str, int] = {}
        # Track tool calls that were already emitted via streaming (to avoid duplicate emission)
        self._streamed_tool_call_ids: set = set()

    async def translate(self, trpc_event: TRPCEvent, thread_id: str, run_id: str) -> AsyncGenerator[BaseEvent, None]:
        """Translate a TRPC event to AG-UI protocol events.

        Args:
            trpc_event: The TRPC event to translate
            thread_id: The AG-UI thread ID
            run_id: The AG-UI run ID

        Yields:
            One or more AG-UI protocol events
        """
        try:
            # Handle AgentCancelledEvent from TRPC - silent termination
            if isinstance(trpc_event, AgentCancelledEvent):
                logger.info("Handling AgentCancelledEvent: %s", trpc_event.error_message)
                # Force close any streaming message for consistency
                async for close_event in self.force_close_streaming_message():
                    yield close_event
                # Don't yield any error event - silent termination
                return

            # Check TRPC streaming state using proper methods
            is_partial = getattr(trpc_event, "partial", False)
            is_final_response = not is_partial

            logger.debug("📥 TRPC Event: partial=%s, is_final_response=%s", is_partial, is_final_response)

            # Skip user events (already in the conversation)
            if trpc_event.author == "user":
                logger.debug("Skipping user event")
                return

            # Handle text content - extract thinking and text parts first
            # IMPORTANT: Thinking parts ONLY appear at the beginning of the event sequence
            # Once non-thinking content (text/function_calls) appears, thinking phase is complete
            thinking_parts = []
            text_parts = []

            if trpc_event.content and trpc_event.content.parts:
                for part in trpc_event.content.parts:
                    if part.text:
                        if getattr(part, "thought", False):
                            thinking_parts.append(part.text)
                        else:
                            text_parts.append(part.text)

            # Check if this is a streaming tool call event
            is_streaming_tool_call = trpc_event.is_streaming_tool_call()

            # Get function calls (skip for streaming tool calls as they are handled separately)
            function_calls = [] if is_streaming_tool_call else trpc_event.get_function_calls()

            # Detect transition from thinking phase to content phase
            has_non_thinking_content = bool(text_parts or function_calls)

            # Handle thinking content
            if thinking_parts:
                # Continue/start thinking stream
                async for event in self._translate_thinking_content(trpc_event, thinking_parts):
                    yield event

            # If non-thinking content appears, finalize thinking
            if has_non_thinking_content and self._is_thinking:
                logger.info("📤 THINKING_TEXT_MESSAGE_CONTENT(Accumulated): %s", self._thinking_text)
                # Finalize the ongoing thinking stream
                timestamp_ms = int(trpc_event.timestamp * 1000)
                end_msg_event = ThinkingTextMessageEndEvent(type=EventType.THINKING_TEXT_MESSAGE_END,
                                                            timestamp=timestamp_ms)
                logger.info("📤 THINKING_TEXT_MESSAGE_END: %s", end_msg_event.model_dump_json())
                yield end_msg_event

                end_event = ThinkingEndEvent(type=EventType.THINKING_END, timestamp=timestamp_ms)
                logger.info("📤 THINKING_END: %s", end_event.model_dump_json())
                yield end_event
                self._is_thinking = False
                self._thinking_text = ""

            # Handle regular text content
            if text_parts:
                async for event in self._translate_text_content(trpc_event, text_parts):
                    yield event

            # Handle streaming tool call events (partial tool call arguments)
            if is_streaming_tool_call:
                async for event in self._translate_streaming_tool_call(trpc_event):
                    yield event

            # call _translate_function_calls function to yield Tool Events (complete tool calls only)
            if function_calls:
                logger.debug("TRPC function calls detected: %s calls", len(function_calls))

                # CRITICAL FIX: End any active text message stream before starting tool calls
                # Per AG-UI protocol: TEXT_MESSAGE_END must be sent before TOOL_CALL_START
                async for event in self.force_close_streaming_message():
                    yield event

                # Close any streaming tool calls before emitting complete tool calls
                async for event in self._close_streaming_tool_calls(trpc_event.timestamp):
                    yield event

                # NOW ACTUALLY YIELD THE EVENTS
                async for event in self._translate_function_calls(function_calls, trpc_event.timestamp):
                    yield event

            # Handle function responses and yield the tool response event
            # this is essential for scenarios when user has to render function response at frontend
            function_responses = trpc_event.get_function_responses()
            if function_responses:
                # Function responses should be emitted to frontend so it can render the response as well
                async for event in self._translate_function_response(function_responses, trpc_event.timestamp):
                    yield event

            # Handle state changes
            if trpc_event.actions and trpc_event.actions.state_delta:
                yield self._create_state_delta_event(trpc_event.actions.state_delta, trpc_event.timestamp)


            # Handle error events - distinguish recoverable tool errors from fatal system errors.
            # Tool execution errors (with function_response) are recoverable: the error is already
            # passed back to the LLM as a tool result, so the LLM can retry or adjust its approach.
            # Only fatal errors (LLM failures, system errors) without function_response should
            # emit RunErrorEvent to terminate the run.
            if trpc_event.is_error() and not function_responses:
                # Fatal system/LLM error - emit RunErrorEvent to terminate the run
                logger.error("Fatal error (non-recoverable), error_code=%s, error_message=%s",
                             trpc_event.error_code, trpc_event.error_message)
                # Force close any streaming message before emitting error
                async for close_event in self.force_close_streaming_message():
                    yield close_event
                error_msg = (trpc_event.error_message
                             or (trpc_event.custom_metadata or {}).get("error")
                             or "Unknown error")
                yield RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=error_msg,
                    code=trpc_event.error_code or "MODEL_ERROR",
                )
                return

            # Handle custom events or metadata
            if trpc_event.custom_metadata:
                timestamp_ms = int(trpc_event.timestamp * 1000)
                yield CustomEvent(
                    type=EventType.CUSTOM,
                    name="trpc_metadata",
                    value=trpc_event.custom_metadata,
                    timestamp=timestamp_ms,
                )

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error translating TRPC event: %s", ex, exc_info=True)
            # Don't yield error events here - let the caller handle errors

    async def _translate_text_content(self, trpc_event: TRPCEvent,
                                      text_parts: List[str]) -> AsyncGenerator[BaseEvent, None]:
        """Translate text content from TRPC event to AG-UI text message events.

        Args:
            trpc_event: The TRPC event containing text content
            text_parts: List of text parts (non-thinking) to translate

        Yields:
            Text message events (START, CONTENT, END)
        """
        if not text_parts:
            return

        # Use proper TRPC streaming detection
        is_partial = getattr(trpc_event, "partial", False)
        is_final_response = not is_partial

        logger.debug("📥 Text event - partial=%s, is_final_response=%s, currently_streaming=%s", is_partial,
                     is_final_response, self._is_streaming)

        if is_final_response:

            # If a final text response wasn't streamed (not generated by an LLM) then deliver it in 3 events
            if not self._is_streaming and not self._text_was_streamed:
                logger.info("⏭️ Deliver non-llm response via message events event_id=%s", trpc_event.invocation_id)

                combined_text = "".join(text_parts)
                timestamp_ms = int(trpc_event.timestamp * 1000)
                message_events = [
                    TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        message_id=trpc_event.invocation_id,
                        role="assistant",
                        timestamp=timestamp_ms,
                    ),
                    TextMessageContentEvent(
                        type=EventType.TEXT_MESSAGE_CONTENT,
                        message_id=trpc_event.invocation_id,
                        delta=combined_text,
                        timestamp=timestamp_ms,
                    ),
                    TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END,
                                        message_id=trpc_event.invocation_id,
                                        timestamp=timestamp_ms),
                ]
                for msg in message_events:
                    yield msg

            logger.debug("⏭️ Skipping final response event (content already streamed)")

            # If we're currently streaming, this final response means we should end the stream
            if self._is_streaming and self._streaming_message_id:
                accumulated_text = "".join(text_parts)
                logger.info("📤 TEXT_MESSAGE_CONTENT(Accumulated): %s", accumulated_text)
                timestamp_ms = int(trpc_event.timestamp * 1000)
                end_event = TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END,
                                                message_id=self._streaming_message_id,
                                                timestamp=timestamp_ms)
                logger.info("📤 TEXT_MESSAGE_END (from final response): %s", end_event.model_dump_json())
                yield end_event

                # Reset streaming state
                self._streaming_message_id = None
                self._is_streaming = False

            self._text_was_streamed = False
            return

        combined_text = "".join(text_parts)  # Don't add newlines for streaming

        # Handle streaming logic
        timestamp_ms = int(trpc_event.timestamp * 1000)

        if not self._is_streaming:
            # Start of new message - emit START event
            self._streaming_message_id = str(uuid.uuid4())
            self._is_streaming = True
            self._text_was_streamed = True

            start_event = TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                message_id=self._streaming_message_id,
                role="assistant",
                timestamp=timestamp_ms,
            )
            logger.info("📤 TEXT_MESSAGE_START: %s", start_event.model_dump_json())
            yield start_event

        # Always emit content (unless empty)
        if combined_text:
            content_event = TextMessageContentEvent(
                type=EventType.TEXT_MESSAGE_CONTENT,
                message_id=self._streaming_message_id,
                delta=combined_text,
                timestamp=timestamp_ms,
            )
            logger.debug("📤 TEXT_MESSAGE_CONTENT: %s", content_event.model_dump_json())
            yield content_event

        # If turn is complete (final response), emit END event
        if is_final_response:
            end_event = TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END,
                                            message_id=self._streaming_message_id,
                                            timestamp=timestamp_ms)
            logger.info("📤 TEXT_MESSAGE_END: %s", end_event.model_dump_json())
            yield end_event

            # Reset streaming state
            self._streaming_message_id = None
            self._is_streaming = False
            logger.info("🏁 Streaming completed, state reset")

    async def translate_lro_function_calls(self, trpc_event: LongRunningEvent) -> AsyncGenerator[BaseEvent, None]:
        """Translate long running function calls from TRPC event to AG-UI tool call events.

        Args:
            trpc_event: The TRPC event containing function calls

        Yields:
            Tool call events (START, ARGS, END)
        """
        # Check if this is a LongRunningEvent
        long_running_function_call = trpc_event.function_call
        if long_running_function_call:
            tool_call_id = long_running_function_call.id
            timestamp_ms = int(trpc_event.timestamp * 1000)

            # Note: Long running tool names are tracked at initialization
            # Individual tool call IDs are handled by the frontend

            # Emit TOOL_CALL_START
            start_event = ToolCallStartEvent(
                type=EventType.TOOL_CALL_START,
                tool_call_id=tool_call_id,
                tool_call_name=long_running_function_call.name,
                parent_message_id=None,
                timestamp=timestamp_ms,
            )
            logger.debug("📤 TOOL_CALL_START: tool=%s, id=%s", long_running_function_call.name, tool_call_id)
            yield start_event

            # Emit TOOL_CALL_ARGS if we have arguments
            if long_running_function_call.args:
                # Convert args to string (JSON format)
                args_str = (json.dumps(long_running_function_call.args, ensure_ascii=False) if isinstance(
                    long_running_function_call.args, dict) else str(long_running_function_call.args))
                logger.debug("📤 TOOL_CALL_ARGS: tool=%s, args=%s", long_running_function_call.name, args_str)
                yield ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS,
                                        tool_call_id=tool_call_id,
                                        delta=args_str,
                                        timestamp=timestamp_ms)

            # Emit TOOL_CALL_END
            logger.debug("📤 TOOL_CALL_END: tool=%s, id=%s", long_running_function_call.name, tool_call_id)
            yield ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id, timestamp=timestamp_ms)

            # Clean up tracking
            self._active_tool_calls.pop(tool_call_id, None)

    async def _translate_streaming_tool_call(self, trpc_event: TRPCEvent) -> AsyncGenerator[BaseEvent, None]:
        """Translate streaming tool call events to AG-UI tool call events.

        This method handles progressive streaming of tool call arguments.
        It emits TOOL_CALL_START on first chunk, TOOL_CALL_ARGS for each chunk,
        and tracks state for proper TOOL_CALL_END emission.

        Only delta mode is supported: uses TOOL_STREAMING_ARGS key, contains only new content.

        Args:
            trpc_event: The TRPC event containing streaming tool call data

        Yields:
            Tool call events (START, ARGS) - END is handled separately
        """
        if not trpc_event.content or not trpc_event.content.parts:
            return

        timestamp_ms = int(trpc_event.timestamp * 1000)

        for part in trpc_event.content.parts:
            if not part.function_call:
                continue

            func_call = part.function_call
            tool_call_id = func_call.id or f"streaming_{uuid.uuid4().hex[:8]}"
            tool_name = func_call.name or "unknown"

            # Skip long-running tools
            if tool_name in self.long_running_tool_names:
                continue

            # Get streaming args - only delta mode is supported
            args = func_call.args or {}

            # Check for delta mode
            streaming_delta = args.get(TOOL_STREAMING_ARGS)
            if streaming_delta is None:
                # Skip tool calls without delta updates
                continue

            # Check if this is the first chunk for this tool call
            if tool_call_id not in self._streaming_tool_calls:
                # First chunk - emit TOOL_CALL_START
                self._streaming_tool_calls[tool_call_id] = 0

                # Close any active text message stream first
                async for close_event in self.force_close_streaming_message():
                    yield close_event

                start_event = ToolCallStartEvent(
                    type=EventType.TOOL_CALL_START,
                    tool_call_id=tool_call_id,
                    tool_call_name=tool_name,
                    parent_message_id=None,
                    timestamp=timestamp_ms,
                )
                logger.debug(f"📤 TOOL_CALL_START (streaming): tool={tool_name}, id={tool_call_id}")
                yield start_event

            # Emit TOOL_CALL_ARGS with delta content
            if streaming_delta:
                # Track that this tool call is active (value is just a placeholder)
                self._streaming_tool_calls[tool_call_id] = True

                args_event = ToolCallArgsEvent(
                    type=EventType.TOOL_CALL_ARGS,
                    tool_call_id=tool_call_id,
                    delta=streaming_delta,
                    timestamp=timestamp_ms,
                )
                logger.debug(f"📤 TOOL_CALL_ARGS (streaming delta): tool={tool_name}, delta_len={len(streaming_delta)}")
                yield args_event

    async def _close_streaming_tool_calls(self, timestamp: float) -> AsyncGenerator[BaseEvent, None]:
        """Close any active streaming tool calls.

        This method emits TOOL_CALL_END for all streaming tool calls that were started
        but not yet completed.

        Args:
            timestamp: The timestamp for the events

        Yields:
            TOOL_CALL_END events for each active streaming tool call
        """
        timestamp_ms = int(timestamp * 1000)

        for tool_call_id in list(self._streaming_tool_calls.keys()):
            end_event = ToolCallEndEvent(
                type=EventType.TOOL_CALL_END,
                tool_call_id=tool_call_id,
                timestamp=timestamp_ms,
            )
            logger.debug(f"📤 TOOL_CALL_END (streaming complete): id={tool_call_id}")
            yield end_event
            self._streamed_tool_call_ids.add(tool_call_id)

        # Clear streaming tool calls state
        self._streaming_tool_calls.clear()

    async def _translate_function_calls(
        self,
        function_calls: list[types.FunctionCall],
        timestamp: float,
    ) -> AsyncGenerator[BaseEvent, None]:
        """Translate function calls from TRPC event to AG-UI tool call events.

        Args:
            function_calls: List of function calls from the event
            timestamp: The timestamp from the TRPC event

        Yields:
            Tool call events (START, ARGS, END)
        """
        # Since we're not tracking streaming messages, use None for parent message
        parent_message_id = None
        timestamp_ms = int(timestamp * 1000)

        for func_call in function_calls:
            tool_call_id = func_call.id
            tool_name = getattr(func_call, "name", "unknown")
            if tool_name in self.long_running_tool_names:
                continue

            if tool_call_id in self._streamed_tool_call_ids:
                self._streamed_tool_call_ids.discard(tool_call_id)
                continue

            # Track the tool call
            self._active_tool_calls[tool_call_id] = tool_call_id

            # Emit TOOL_CALL_START
            start_event = ToolCallStartEvent(
                type=EventType.TOOL_CALL_START,
                tool_call_id=tool_call_id,
                tool_call_name=func_call.name,
                parent_message_id=parent_message_id,
                timestamp=timestamp_ms,
            )
            logger.debug("📤 TOOL_CALL_START: tool=%s, id=%s", func_call.name, tool_call_id)
            yield start_event

            # Emit TOOL_CALL_ARGS if we have arguments
            if hasattr(func_call, "args") and func_call.args:
                # Convert args to string (JSON format)
                args_str = (json.dumps(func_call.args, ensure_ascii=False)
                            if isinstance(func_call.args, dict) else str(func_call.args))
                logger.debug("📤 TOOL_CALL_ARGS: tool=%s, args=%s", func_call.name, args_str)
                yield ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS,
                                        tool_call_id=tool_call_id,
                                        delta=args_str,
                                        timestamp=timestamp_ms)

            # Emit TOOL_CALL_END
            logger.debug("📤 TOOL_CALL_END: tool=%s, id=%s", func_call.name, tool_call_id)
            yield ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id, timestamp=timestamp_ms)

            # Clean up tracking
            self._active_tool_calls.pop(tool_call_id, None)

    async def _translate_function_response(
        self,
        function_response: list[types.FunctionResponse],
        timestamp: float,
    ) -> AsyncGenerator[BaseEvent, None]:
        """Translate function calls from TRPC event to AG-UI tool call events.

        Args:
            function_response: List of function response from the event
            timestamp: The timestamp from the TRPC event

        Yields:
            Tool result events (only for tools not in long_running_tool_names)
        """
        timestamp_ms = int(timestamp * 1000)

        for func_response in function_response:
            tool_call_id = func_response.id
            tool_name = getattr(func_response, "name", "unknown")
            # Only emit ToolCallResultEvent for tools which are not long_running_tool
            # this is because long running tools are handled by the frontend
            if tool_name not in self.long_running_tool_names:
                response_content = json.dumps(func_response.response, ensure_ascii=False)
                logger.info("📤 TOOL_CALL_RESULT: tool=%s, id=%s, response=%s", tool_name, tool_call_id,
                            response_content)
                yield ToolCallResultEvent(
                    message_id=str(uuid.uuid4()),
                    type=EventType.TOOL_CALL_RESULT,
                    tool_call_id=tool_call_id,
                    content=response_content,
                    timestamp=timestamp_ms,
                )
            else:
                logger.debug("Skipping ToolCallResultEvent for long-running tool: %s (ID: %s)", tool_name, tool_call_id)

    def _create_state_delta_event(self, state_delta: Dict[str, Any], timestamp: float) -> StateDeltaEvent:
        """Create a state delta event from TRPC state changes.

        Args:
            state_delta: The state changes from TRPC
            timestamp: The timestamp from the TRPC event

        Returns:
            A StateDeltaEvent
        """
        # Convert to JSON Patch format (RFC 6902)
        # Use "add" operation which works for both new and existing paths
        patches = []
        for key, value in state_delta.items():
            patches.append({"op": "add", "path": f"/{key}", "value": value})

        timestamp_ms = int(timestamp * 1000)
        return StateDeltaEvent(type=EventType.STATE_DELTA, delta=patches, timestamp=timestamp_ms)

    def _create_state_snapshot_event(
        self,
        state_snapshot: Dict[str, Any],
        timestamp: float,
    ) -> StateSnapshotEvent:
        """Create a state snapshot event from TRPC state changes.

        Args:
            state_snapshot: The state changes from TRPC
            timestamp: The timestamp from the TRPC event

        Returns:
            A StateSnapshotEvent
        """
        timestamp_ms = int(timestamp * 1000)
        return StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=state_snapshot, timestamp=timestamp_ms)

    async def force_close_streaming_message(self) -> AsyncGenerator[BaseEvent, None]:
        """Force close any open streaming message.

        This should be called before ending a run to ensure proper message termination.

        Yields:
            TEXT_MESSAGE_END event if there was an open streaming message
        """
        if self._is_streaming and self._streaming_message_id:
            logger.warning("🚨 Force-closing unterminated streaming message: %s", self._streaming_message_id)

            # Generate current timestamp since there's no TRPC event available
            from datetime import datetime

            timestamp_ms = int(datetime.now().timestamp() * 1000)

            end_event = TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END,
                                            message_id=self._streaming_message_id,
                                            timestamp=timestamp_ms)
            logger.debug("📤 TEXT_MESSAGE_END (forced): %s", end_event.model_dump_json())
            yield end_event

            # Reset streaming state
            self._streaming_message_id = None
            self._is_streaming = False
            logger.debug("🔄 Streaming state reset after force-close")

    async def _translate_thinking_content(self, trpc_event: TRPCEvent,
                                          thinking_parts: List[str]) -> AsyncGenerator[BaseEvent, None]:
        """Translate thinking content from TRPC event to AG-UI thinking message events.

        This method handles ONLY the thinking phase, which appears at the beginning of event sequences.
        Once non-thinking content appears, this method should no longer be called.

        Args:
            trpc_event: The TRPC event containing thinking content
            thinking_parts: List of thinking text parts

        Yields:
            Thinking message events (START, CONTENT, END)
        """
        if not thinking_parts:
            return

        timestamp_ms = int(trpc_event.timestamp * 1000)
        combined_text = "".join(thinking_parts)
        is_partial = getattr(trpc_event, "partial", False)

        logger.debug("💭 Thinking event - partial=%s, currently_thinking=%s, text_len=%s", is_partial, self._is_thinking,
                     len(combined_text))

        # Final response (not partial) - emit complete block
        if not is_partial:
            if self._is_thinking:
                # Close the streaming thinking with final content
                logger.info("📤 THINKING_TEXT_MESSAGE_CONTENT(Accumulated): %s", combined_text)

                end_msg_event = ThinkingTextMessageEndEvent(type=EventType.THINKING_TEXT_MESSAGE_END,
                                                            timestamp=timestamp_ms)
                logger.info("📤 THINKING_TEXT_MESSAGE_END: %s", end_msg_event.model_dump_json())
                yield end_msg_event

                end_event = ThinkingEndEvent(type=EventType.THINKING_END, timestamp=timestamp_ms)
                logger.info("📤 THINKING_END: %s", end_event.model_dump_json())
                yield end_event
                self._thinking_text = ""
                self._is_thinking = False
            else:
                # If thinking is not active, it means the thinking phase was already closed
                # by the transition logic in translate() method (lines 122-133).
                # Do NOT emit a new complete thinking block to avoid duplicates.
                logger.debug("⏭️ Skipping final thinking event (already closed by transition logic)")
            return

        # Streaming thinking (partial)
        if not self._is_thinking:
            # Start new thinking stream
            self._is_thinking = True
            start_event = ThinkingStartEvent(type=EventType.THINKING_START, timestamp=timestamp_ms)
            logger.info("📤 THINKING_START: %s", start_event.model_dump_json())
            yield start_event

            start_msg_event = ThinkingTextMessageStartEvent(type=EventType.THINKING_TEXT_MESSAGE_START,
                                                            timestamp=timestamp_ms)
            logger.info("📤 THINKING_TEXT_MESSAGE_START: %s", start_msg_event.model_dump_json())
            yield start_msg_event

        # Emit content delta
        content_event = ThinkingTextMessageContentEvent(type=EventType.THINKING_TEXT_MESSAGE_CONTENT,
                                                        delta=combined_text,
                                                        timestamp=timestamp_ms)
        logger.debug("📤 THINKING_TEXT_MESSAGE_CONTENT: %s", content_event.model_dump_json())
        self._thinking_text += combined_text
        yield content_event

    def reset(self):
        """Reset the translator state.

        This should be called between different conversation runs
        to ensure clean state.
        """
        self._active_tool_calls.clear()
        self._streaming_message_id = None
        self._is_streaming = False
        self._text_was_streamed = False
        self._is_thinking = False
        self._thinking_text = ""
        self._streaming_tool_calls.clear()
        self._streamed_tool_call_ids.clear()
        # Note: long_running_tool_names are not cleared as they are set at initialization
        logger.debug("Reset EventTranslator state (including streaming and thinking state)")
