# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Event writers for graph nodes.

This module provides EventWriter (sync) and AsyncEventWriter (async)
for emitting streaming events during execution, wrapping the engine stream writer.

All events use the unified Event class from trpc_agent_sdk.events via EventBuilder.
"""

import asyncio
from datetime import datetime
from typing import Any
from typing import Callable
from typing import Optional

from google.genai.types import Content
from google.genai.types import Part

from trpc_agent_sdk.events import Event

from ._constants import STREAM_KEY_ACK
from ._constants import STREAM_KEY_EVENT
from ._events import EventBuilder


class EventWriterBase:
    """Base class for graph event writers.

    Stores shared context and helpers for building events.
    """

    def __init__(
        self,
        writer: Callable[[dict[str, Any]], None],
        invocation_id: str,
        author: str,
        branch: str,
        request_id: Optional[str] = None,
        parent_invocation_id: Optional[str] = None,
        filter_key: Optional[str] = None,
    ):
        """Initialize the event writer base.

        Args:
            writer: Engine stream writer function
            invocation_id: Current invocation ID
            author: Name of the node/agent writing events
            branch: Current branch in the agent tree
            request_id: Optional request ID for tracing (from context)
            parent_invocation_id: Optional parent invocation ID for sub-agent executions
            filter_key: Optional hierarchical filter key
        """
        self._writer = writer
        self._builder = EventBuilder(invocation_id, author, branch)

        # Store optional context fields
        self._request_id = request_id
        self._parent_invocation_id = parent_invocation_id
        self._filter_key = filter_key

        # Track start times for duration calculation
        self._node_start_time: Optional[datetime] = None
        self._model_start_time: Optional[datetime] = None
        self._tool_start_times: dict[str, datetime] = {}

    def _apply_optional_context(self, event: Event) -> None:
        if self._request_id:
            event.request_id = self._request_id
        if self._parent_invocation_id:
            event.parent_invocation_id = self._parent_invocation_id
        if self._filter_key:
            event.filter_key = self._filter_key

    def _emit_event(self, event: Event) -> None:
        self._writer({STREAM_KEY_EVENT: event})

    def _build_text_event(self, text: str, partial: bool = True) -> Event:
        content = Content(role="model", parts=[Part.from_text(text=text)])
        event = Event(
            invocation_id=self._builder.invocation_id,
            author=self._builder.author,
            branch=self._builder.branch,
            content=content,
            partial=partial,
        )
        self._apply_optional_context(event)
        return event

    def _build_content_event(self, content: Content, partial: bool = False) -> Event:
        event = Event(
            invocation_id=self._builder.invocation_id,
            author=self._builder.author,
            branch=self._builder.branch,
            content=content,
            partial=partial,
        )
        self._apply_optional_context(event)
        return event

    def _build_node_start_event(
        self,
        node_id: str,
        node_type: str = "function",
        step_number: int = 0,
        input_keys: Optional[list[str]] = None,
        node_description: Optional[str] = None,
    ) -> Event:
        self._node_start_time = datetime.now()
        return self._builder.node_start(
            node_id=node_id,
            node_type=node_type,
            node_description=node_description,
            step_number=step_number,
            input_keys=input_keys,
        )

    def _build_node_complete_event(
        self,
        node_id: str,
        node_type: str = "function",
        step_number: int = 0,
        output_keys: Optional[list[str]] = None,
        node_description: Optional[str] = None,
    ) -> Event:
        return self._builder.node_complete(
            node_id=node_id,
            node_type=node_type,
            node_description=node_description,
            step_number=step_number,
            start_time=self._node_start_time,
            output_keys=output_keys,
        )

    def _build_node_error_event(
        self,
        node_id: str,
        error: str,
        node_type: str = "function",
        step_number: int = 0,
        node_description: Optional[str] = None,
    ) -> Event:
        return self._builder.node_error(
            node_id=node_id,
            error=error,
            node_type=node_type,
            node_description=node_description,
            step_number=step_number,
            start_time=self._node_start_time,
        )

    def _build_model_start_event(self, model_name: str, input_text: str = "") -> Event:
        self._model_start_time = datetime.now()
        return self._builder.model_start(model_name, self._builder.author, input_text)

    def _build_model_complete_event(
        self,
        model_name: str,
        start_time: Optional[datetime] = None,
        input_text: str = "",
        output_text: str = "",
        error: Optional[str] = None,
    ) -> Event:
        actual_start_time = start_time or self._model_start_time
        return self._builder.model_complete(model_name, self._builder.author, actual_start_time, input_text,
                                            output_text, error)

    def _build_tool_start_event(self, tool_name: str, tool_id: str, input_args: str = "") -> Event:
        self._tool_start_times[tool_id] = datetime.now()
        return self._builder.tool_start(tool_name, tool_id, self._builder.author, input_args)

    def _build_tool_complete_event(
        self,
        tool_name: str,
        tool_id: str,
        start_time: Optional[datetime] = None,
        input_args: str = "",
        output_result: str = "",
        error: Optional[str] = None,
    ) -> Event:
        actual_start_time = start_time or self._tool_start_times.get(tool_id)
        return self._builder.tool_complete(tool_name, tool_id, self._builder.author, actual_start_time, input_args,
                                           output_result, error)

    def _clear_tool_start_time(self, tool_id: str) -> None:
        self._tool_start_times.pop(tool_id, None)

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def invocation_id(self) -> str:
        """Get the current invocation ID."""
        return self._builder.invocation_id

    @property
    def author(self) -> str:
        """Get the current author (node name)."""
        return self._builder.author

    @property
    def branch(self) -> str:
        """Get the current branch."""
        return self._builder.branch

    @property
    def builder(self) -> EventBuilder:
        """Get the EventBuilder for advanced event creation."""
        return self._builder


class EventWriter(EventWriterBase):
    """Event writer for graph nodes.

    Wraps the engine's StreamWriter to write TRPC-Agent Events.
    Nodes receive an EventWriter to stream partial results during execution.

    Internally uses EventBuilder for consistent event creation.

    Example:
        >>> async def my_node(state: State, writer: EventWriter) -> dict:
        ...     writer.write_text("Processing...")
        ...     # Do some work
        ...     writer.write_text("Done!", partial=False)
        ...     return {"result": "completed"}
    """

    # =========================================================================
    # Basic Event Methods
    # =========================================================================

    def write_text(self, text: str, partial: bool = True) -> None:
        """Write a text response event."""
        self._emit_event(self._build_text_event(text, partial))

    def write_content(self, content: Content, partial: bool = False) -> None:
        """Write a Content object as event."""
        self._emit_event(self._build_content_event(content, partial))

    def write_event(self, event: Event) -> None:
        """Write any TRPC-Agent Event directly."""
        self._emit_event(event)


class AsyncEventWriter(EventWriterBase):
    """Async wrapper for EventWriterBase.

    Provides an awaitable interface to emit events and yield control so
    queued stream events can flush promptly.
    """

    async def _emit_and_flush(self, event: Event) -> None:
        """Emit an event and wait until GraphAgent consumes it."""
        ack = asyncio.get_running_loop().create_future()
        self._writer({STREAM_KEY_EVENT: event, STREAM_KEY_ACK: ack})
        await ack

    # =========================================================================
    # Basic Event Methods
    # =========================================================================

    async def write_text(self, text: str, partial: bool = True) -> None:
        """Write a text response event."""
        await self._emit_and_flush(self._build_text_event(text, partial))

    async def write_content(self, content: Content, partial: bool = False) -> None:
        """Write a Content object as event."""
        await self._emit_and_flush(self._build_content_event(content, partial))

    async def write_event(self, event: Event) -> None:
        """Write any TRPC-Agent Event directly."""
        await self._emit_and_flush(event)

    # =========================================================================
    # Node Execution Events
    # =========================================================================

    async def write_node_start(
        self,
        node_id: str,
        node_type: str = "function",
        step_number: int = 0,
        input_keys: Optional[list[str]] = None,
        node_description: Optional[str] = None,
    ) -> None:
        """Write a node execution start event."""
        await self._emit_and_flush(
            self._build_node_start_event(
                node_id=node_id,
                node_type=node_type,
                node_description=node_description,
                step_number=step_number,
                input_keys=input_keys,
            ))

    async def write_node_complete(
        self,
        node_id: str,
        node_type: str = "function",
        step_number: int = 0,
        output_keys: Optional[list[str]] = None,
        node_description: Optional[str] = None,
    ) -> None:
        """Write a node execution complete event."""
        await self._emit_and_flush(
            self._build_node_complete_event(
                node_id=node_id,
                node_type=node_type,
                node_description=node_description,
                step_number=step_number,
                output_keys=output_keys,
            ))

    async def write_node_error(
        self,
        node_id: str,
        error: str,
        node_type: str = "function",
        step_number: int = 0,
        node_description: Optional[str] = None,
    ) -> None:
        """Write a node execution error event."""
        await self._emit_and_flush(
            self._build_node_error_event(
                node_id=node_id,
                error=error,
                node_type=node_type,
                node_description=node_description,
                step_number=step_number,
            ))

    # =========================================================================
    # Model Execution Events
    # =========================================================================

    async def write_model_start(self, model_name: str, input_text: str = "") -> None:
        """Write a model execution start event."""
        await self._emit_and_flush(self._build_model_start_event(model_name, input_text))

    async def write_model_complete(
        self,
        model_name: str,
        start_time: Optional[datetime] = None,
        input_text: str = "",
        output_text: str = "",
        error: Optional[str] = None,
    ) -> None:
        """Write a model execution complete event."""
        await self._emit_and_flush(
            self._build_model_complete_event(
                model_name,
                start_time=start_time,
                input_text=input_text,
                output_text=output_text,
                error=error,
            ))

    # =========================================================================
    # Tool Execution Events
    # =========================================================================

    async def write_tool_start(self, tool_name: str, tool_id: str, input_args: str = "") -> None:
        """Write a tool execution start event."""
        await self._emit_and_flush(self._build_tool_start_event(tool_name, tool_id, input_args))

    async def write_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        start_time: Optional[datetime] = None,
        input_args: str = "",
        output_result: str = "",
        error: Optional[str] = None,
    ) -> None:
        """Write a tool execution complete event."""
        event = self._build_tool_complete_event(
            tool_name,
            tool_id,
            start_time=start_time,
            input_args=input_args,
            output_result=output_result,
            error=error,
        )
        self._clear_tool_start_time(tool_id)
        await self._emit_and_flush(event)
