# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""LangGraph event writer for emitting trpc Events via StreamWriter."""

from enum import Enum
from typing import Any
from typing import Dict

from google.genai import types

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger

from ._langgraph import get_agent_context

# Marker to identify trpc Events in LangGraph custom stream
TRPC_EVENT_MARKER = "__trpc_event__"

# Marker for event type (text, custom, etc.)
LANGGRAPH_EVENT_TYPE = "__langgraph_event_type__"


class LangGraphEventType(str, Enum):
    """Enum for LangGraph event types.

    This enum defines the types of events that can be emitted via LangGraphEventWriter.
    """
    TEXT = "text"
    """Text event type for text messages"""

    CUSTOM = "custom"
    """Custom event type for structured data"""


class _TrpcEventWrapper:
    """Wraps Event for StreamWriter transport.

    This wrapper adds markers that allow LangGraphAgent to detect and extract
    trpc Events from the custom stream.
    """

    def __init__(self, event: Event, event_type: LangGraphEventType):
        """Initialize the wrapper.

        Args:
            event: The trpc Event to wrap
            event_type: The type of event
        """
        self._event = event
        self._event_type = event_type

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for StreamWriter transport.

        Returns:
            Dictionary containing the wrapped event with markers
        """
        return {
            TRPC_EVENT_MARKER: True,
            LANGGRAPH_EVENT_TYPE: self._event_type.value,
            "event": self._event,
        }


class LangGraphEventWriter:
    """Writer for emitting trpc Events from LangGraph nodes via StreamWriter.

    This class provides a convenient interface for LangGraph nodes to emit
    trpc Events that will be properly handled by LangGraphAgent and translated
    to protocol-specific events (AG-UI, A2A).

    Usage:
        async def my_node(state, config, writer: StreamWriter):
            event_writer = LangGraphEventWriter.from_config(writer, config)
            event_writer.write_text("Processing...")
            event_writer.write_custom({"progress": 50})
            return {"messages": [...]}
    """

    def __init__(self, writer: Any, ctx: InvocationContext):
        """Initialize the event writer.

        Args:
            writer: LangGraph StreamWriter instance
            ctx: The InvocationContext for this invocation
        """
        self._writer = writer
        self._ctx = ctx

    @classmethod
    def from_config(cls, writer: Any, config: Dict[str, Any]) -> "LangGraphEventWriter":
        """Create a LangGraphEventWriter from StreamWriter and RunnableConfig.

        Args:
            writer: LangGraph StreamWriter instance
            config: RunnableConfig containing InvocationContext

        Returns:
            LangGraphEventWriter instance

        Raises:
            ValueError: If InvocationContext is not found in config
        """
        ctx = get_agent_context(config)
        return cls(writer, ctx)

    def write_text(
        self,
        text: str,
        *,
        partial: bool = True,
        thought: bool = False,
    ) -> None:
        """Write a text event.

        Args:
            text: The text content to emit
            partial: Whether this is a partial/streaming event (default: True)
            thought: Whether this is a thinking/reasoning text (default: False)
        """
        part = types.Part.from_text(text=text)
        if thought:
            part.thought = True

        event = Event(
            invocation_id=self._ctx.invocation_id,
            author=self._ctx.agent_name,
            branch=self._ctx.branch,
            content=types.Content(role="model", parts=[part]),
            partial=partial,
            custom_metadata={
                TRPC_EVENT_MARKER: True,
                LANGGRAPH_EVENT_TYPE: LangGraphEventType.TEXT.value
            },
        )
        self._emit(event, LangGraphEventType.TEXT)

    def write_custom(self, data: Dict[str, Any]) -> None:
        """Write a custom data event.

        Args:
            data: Custom data dictionary to emit
        """
        event = Event(
            invocation_id=self._ctx.invocation_id,
            author=self._ctx.agent_name,
            branch=self._ctx.branch,
            custom_metadata={
                TRPC_EVENT_MARKER: True,
                LANGGRAPH_EVENT_TYPE: LangGraphEventType.CUSTOM.value,
                "data": data,
            },
            partial=True,
        )
        self._emit(event, LangGraphEventType.CUSTOM)

    def _emit(self, event: Event, event_type: LangGraphEventType) -> None:
        """Internal method to emit event via StreamWriter.

        Args:
            event: The Event to emit
            event_type: The type of event for detection
        """
        wrapper = _TrpcEventWrapper(event, event_type)
        self._writer(wrapper.to_dict())
        logger.debug("Emitted LangGraph event: type=%s, invocation_id=%s", event_type.value, event.invocation_id)


def is_trpc_event_chunk(chunk_data: Any) -> bool:
    """Check if chunk contains a trpc Event from LangGraphEventWriter.

    This function is used by LangGraphAgent to detect events emitted via
    LangGraphEventWriter in the custom stream.

    Args:
        chunk_data: The chunk data from LangGraph streaming

    Returns:
        True if the chunk contains a trpc Event, False otherwise
    """
    if isinstance(chunk_data, dict):
        return chunk_data.get(TRPC_EVENT_MARKER, False) is True
    return False


def extract_trpc_event(chunk_data: Dict[str, Any]) -> Event:
    """Extract the trpc Event from a custom stream chunk.

    This function is used by LangGraphAgent to extract Events that were
    emitted via LangGraphEventWriter.

    Args:
        chunk_data: The chunk data containing a wrapped trpc Event

    Returns:
        The extracted Event

    Raises:
        ValueError: If the chunk does not contain a valid Event
    """
    if not is_trpc_event_chunk(chunk_data):
        raise ValueError("Chunk does not contain a trpc Event")

    event = chunk_data.get("event")
    if not isinstance(event, Event):
        raise ValueError(f"Invalid event in chunk: expected Event, got {type(event)}")

    return event


def get_event_type(event: Event) -> LangGraphEventType | None:
    """Get the LangGraph event type from a trpc Event.

    Args:
        event: The trpc Event to extract type from

    Returns:
        LangGraphEventType if found, None otherwise
    """
    if not event.custom_metadata or LANGGRAPH_EVENT_TYPE not in event.custom_metadata:
        return None

    event_type_str = event.custom_metadata.get(LANGGRAPH_EVENT_TYPE)
    try:
        return LangGraphEventType(event_type_str)
    except ValueError:
        logger.warning("Unknown LangGraph event type: %s", event_type_str)
        return None
