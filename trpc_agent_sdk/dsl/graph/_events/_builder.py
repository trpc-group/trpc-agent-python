# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""EventBuilder for graph execution events.

This module provides the EventBuilder class that creates Event objects for
various graph execution lifecycle events. All events use the unified Event
class from trpc_agent_sdk.events.

The events are distinguished by:
1. The content text (describing what happened)
2. The actions.state_delta (containing structured event data)
3. The partial flag (indicating streaming vs final events)

Event types:
- Node execution events (start, complete, error)
- Model execution events (LLM calls)
- Tool execution events
- Graph completion events

Example:
    >>> builder = EventBuilder(invocation_id="inv-123", author="my_agent", branch="main")
    >>> start_event = builder.node_start("my_node")
    >>> complete_event = builder.node_complete("my_node", start_time=start_time)
"""

from datetime import datetime
from typing import Any
from typing import Optional

from google.genai.types import Content
from google.genai.types import Part

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import EventActions

from ._constants import ExecutionPhase
from ._constants import GRAPH_EXECUTION_KEY_END_TIME
from ._constants import GRAPH_EXECUTION_KEY_ERROR
from ._constants import GRAPH_EXECUTION_KEY_FINAL_STATE_KEYS
from ._constants import GRAPH_EXECUTION_KEY_PHASE
from ._constants import GRAPH_EXECUTION_KEY_START_TIME
from ._constants import GRAPH_EXECUTION_KEY_STATE_DELTA_KEYS
from ._constants import GRAPH_EXECUTION_KEY_TOTAL_DURATION_MS
from ._constants import GRAPH_EXECUTION_KEY_TOTAL_STEPS
from ._constants import GraphEventType
from ._constants import METADATA_KEY_MODEL
from ._constants import METADATA_KEY_NODE
from ._constants import METADATA_KEY_STATE
from ._constants import METADATA_KEY_TOOL
from ._metadata import ModelExecutionMetadata
from ._metadata import NodeExecutionMetadata
from ._metadata import StateUpdateMetadata
from ._metadata import ToolExecutionMetadata
from ._metadata import _store_metadata


class EventBuilder:
    """Builder for creating graph execution events.

    Centralizes event creation logic with common fields (invocation_id, author,
    branch) set once and reused across multiple event creations.

    Example:
        >>> builder = EventBuilder(invocation_id="inv-123", author="my_node", branch="main")
        >>> start_event = builder.node_start("my_node")
        >>> complete_event = builder.node_complete("my_node", start_time=start_time)
    """

    def __init__(
        self,
        invocation_id: str,
        author: str = "",
        branch: str = "",
    ):
        """Initialize the EventBuilder.

        Args:
            invocation_id: Current invocation ID (required for all events)
            author: Default event author (can be overridden per event)
            branch: Current branch in the agent tree
        """
        self._invocation_id = invocation_id
        self._author = author
        self._branch = branch

    @property
    def invocation_id(self) -> str:
        """Get the invocation ID."""
        return self._invocation_id

    @property
    def author(self) -> str:
        """Get the default author."""
        return self._author

    @property
    def branch(self) -> str:
        """Get the branch."""
        return self._branch

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _build_event(
        self,
        text: str,
        state_delta: dict[str, Any],
        partial: bool = False,
        visible: bool = True,
        author_override: Optional[str] = None,
        object_type: Optional[str] = None,
    ) -> Event:
        """Build an Event with common fields.

        Args:
            text: Human-readable description
            state_delta: State delta dictionary (may contain structured metadata)
            partial: Whether this is a partial/streaming event
            visible: Whether this event is visible to observers
            author_override: Override for the event author
            object_type: Object type constant for event classification

        Returns:
            Configured Event instance
        """
        content = Content(role="model", parts=[Part.from_text(text=text)])
        event = Event(
            invocation_id=self._invocation_id,
            author=author_override or self._author,
            branch=self._branch,
            content=content,
            partial=partial,
            visible=visible,
            actions=EventActions(state_delta=state_delta),
        )
        # Set object type if provided
        if object_type:
            event.object = object_type
        return event

    def _calc_duration_ms(self, start_time: Optional[datetime], end_time: datetime) -> float:
        """Calculate duration in milliseconds."""
        if start_time:
            return (end_time - start_time).total_seconds() * 1000
        return 0.0

    def _truncate(self, text: str, max_len: int = 500) -> str:
        """Truncate text to max length."""
        return text[:max_len] if text else ""

    # =========================================================================
    # Node Events
    # =========================================================================

    def node_start(
        self,
        node_id: str,
        node_type: str = "function",
        step_number: int = 0,
        input_keys: Optional[list[str]] = None,
        model_name: Optional[str] = None,
        model_input: Optional[str] = None,
        node_description: Optional[str] = None,
    ) -> Event:
        """Create a node execution start event with structured metadata."""
        start_time = datetime.now()

        # Create structured metadata
        metadata = NodeExecutionMetadata(
            node_id=node_id,
            node_type=node_type,
            node_description=node_description,
            phase=ExecutionPhase.START.value,
            start_time=start_time.isoformat(),
            step_number=step_number,
            input_keys=input_keys or [],
            model_name=model_name,
            model_input=self._truncate(model_input, 500) if model_input else None,
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_NODE, metadata)

        return self._build_event(
            text=f"Starting node: {node_id}",
            state_delta=state_delta,
            partial=True,
            author_override=self._author or node_id,
            object_type=GraphEventType.GRAPH_NODE_START,
        )

    def node_complete(
        self,
        node_id: str,
        node_type: str = "function",
        step_number: int = 0,
        start_time: Optional[datetime] = None,
        output_keys: Optional[list[str]] = None,
        tool_calls: Optional[list[dict[str, Any]]] = None,
        model_name: Optional[str] = None,
        node_description: Optional[str] = None,
    ) -> Event:
        """Create a node execution complete event with structured metadata."""
        end_time = datetime.now()
        duration_ms = self._calc_duration_ms(start_time, end_time)

        # Create structured metadata
        metadata = NodeExecutionMetadata(
            node_id=node_id,
            node_type=node_type,
            node_description=node_description,
            phase=ExecutionPhase.COMPLETE.value,
            start_time=start_time.isoformat() if start_time else None,
            end_time=end_time.isoformat(),
            duration_ms=duration_ms,
            step_number=step_number,
            output_keys=output_keys or [],
            tool_calls=tool_calls or [],
            model_name=model_name,
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_NODE, metadata)

        return self._build_event(
            text=f"Completed node: {node_id} ({duration_ms:.1f}ms)",
            state_delta=state_delta,
            author_override=self._author or node_id,
            object_type=GraphEventType.GRAPH_NODE_COMPLETE,
        )

    def node_error(
        self,
        node_id: str,
        error: str,
        node_type: str = "function",
        step_number: int = 0,
        start_time: Optional[datetime] = None,
        node_description: Optional[str] = None,
    ) -> Event:
        """Create a node execution error event with structured metadata."""
        end_time = datetime.now()
        duration_ms = self._calc_duration_ms(start_time, end_time)

        # Create structured metadata
        metadata = NodeExecutionMetadata(
            node_id=node_id,
            node_type=node_type,
            node_description=node_description,
            phase=ExecutionPhase.ERROR.value,
            start_time=start_time.isoformat() if start_time else None,
            end_time=end_time.isoformat(),
            duration_ms=duration_ms,
            step_number=step_number,
            error=error,
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_NODE, metadata)

        # Build error message
        text = f"Error in node {node_id}: {error}"

        return self._build_event(
            text=text,
            state_delta=state_delta,
            author_override=self._author or node_id,
            object_type=GraphEventType.GRAPH_NODE_ERROR,
        )

    # =========================================================================
    # Model Events
    # =========================================================================

    def model_start(
        self,
        model_name: str,
        node_id: str,
        input_text: str = "",
        step_number: int = 0,
    ) -> Event:
        """Create a model execution start event with structured metadata."""
        start_time = datetime.now()

        # Create structured metadata
        metadata = ModelExecutionMetadata(
            model_name=model_name,
            node_id=node_id,
            phase=ExecutionPhase.START.value,
            start_time=start_time.isoformat(),
            input_text=self._truncate(input_text, 500),
            step_number=step_number,
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_MODEL, metadata)

        return self._build_event(
            text=f"Calling model: {model_name}",
            state_delta=state_delta,
            partial=True,
            author_override=self._author or node_id,
            object_type=GraphEventType.GRAPH_NODE_EXECUTION,
        )

    def model_complete(
        self,
        model_name: str,
        node_id: str,
        start_time: Optional[datetime] = None,
        input_text: str = "",
        output_text: str = "",
        error: Optional[str] = None,
        step_number: int = 0,
    ) -> Event:
        """Create a model execution complete event with structured metadata."""
        end_time = datetime.now()
        duration_ms = self._calc_duration_ms(start_time, end_time)
        phase = ExecutionPhase.ERROR.value if error else ExecutionPhase.COMPLETE.value

        # Create structured metadata
        metadata = ModelExecutionMetadata(
            model_name=model_name,
            node_id=node_id,
            phase=phase,
            start_time=start_time.isoformat() if start_time else None,
            end_time=end_time.isoformat(),
            duration_ms=duration_ms,
            input_text=self._truncate(input_text, 500),
            output_text=self._truncate(output_text, 500),
            error=error,
            step_number=step_number,
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_MODEL, metadata)

        if error:
            text = f"Model {model_name} failed: {error}"
        else:
            text = f"Model {model_name} completed ({duration_ms:.1f}ms)"

        return self._build_event(
            text=text,
            state_delta=state_delta,
            author_override=self._author or node_id,
            object_type=GraphEventType.GRAPH_NODE_EXECUTION,
        )

    # =========================================================================
    # Tool Events
    # =========================================================================

    def tool_start(
        self,
        tool_name: str,
        tool_id: str,
        node_id: str,
        input_args: str = "",
    ) -> Event:
        """Create a tool execution start event with structured metadata."""
        start_time = datetime.now()

        # Create structured metadata
        metadata = ToolExecutionMetadata(
            tool_name=tool_name,
            tool_id=tool_id,
            node_id=node_id,
            phase=ExecutionPhase.START.value,
            start_time=start_time.isoformat(),
            input_args=self._truncate(input_args, 1000),
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_TOOL, metadata)

        return self._build_event(
            text=f"Calling tool: {tool_name}",
            state_delta=state_delta,
            partial=True,
            author_override=self._author or node_id,
            object_type=GraphEventType.GRAPH_NODE_EXECUTION,
        )

    def tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        node_id: str,
        start_time: Optional[datetime] = None,
        input_args: str = "",
        output_result: str = "",
        error: Optional[str] = None,
    ) -> Event:
        """Create a tool execution complete event with structured metadata."""
        end_time = datetime.now()
        duration_ms = self._calc_duration_ms(start_time, end_time)
        phase = ExecutionPhase.ERROR.value if error else ExecutionPhase.COMPLETE.value

        # Create structured metadata
        metadata = ToolExecutionMetadata(
            tool_name=tool_name,
            tool_id=tool_id,
            node_id=node_id,
            phase=phase,
            start_time=start_time.isoformat() if start_time else None,
            end_time=end_time.isoformat(),
            duration_ms=duration_ms,
            input_args=self._truncate(input_args, 1000),
            output_result=self._truncate(output_result, 1000),
            error=error,
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_TOOL, metadata)

        if error:
            text = f"Tool {tool_name} failed: {error}"
        else:
            text = f"Tool {tool_name} completed ({duration_ms:.1f}ms)"

        return self._build_event(
            text=text,
            state_delta=state_delta,
            author_override=self._author or node_id,
            object_type=GraphEventType.GRAPH_NODE_EXECUTION,
        )

    # =========================================================================
    # Graph Events
    # =========================================================================

    def graph_complete(
        self,
        total_steps: int,
        start_time: datetime,
        final_state: Optional[dict[str, Any]] = None,
        state_delta: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Event:
        """Create a graph completion event."""
        end_time = datetime.now()
        duration_ms = self._calc_duration_ms(start_time, end_time)
        phase = ExecutionPhase.ERROR.value if error else ExecutionPhase.COMPLETE.value

        if error:
            text = f"Graph execution failed after {total_steps} steps: {error}"
        else:
            text = f"Graph execution completed in {total_steps} steps ({duration_ms:.1f}ms)"

        return self._build_event(
            text=text,
            state_delta={
                GRAPH_EXECUTION_KEY_PHASE: phase,
                GRAPH_EXECUTION_KEY_TOTAL_STEPS: total_steps,
                GRAPH_EXECUTION_KEY_TOTAL_DURATION_MS: duration_ms,
                GRAPH_EXECUTION_KEY_START_TIME: start_time.isoformat(),
                GRAPH_EXECUTION_KEY_END_TIME: end_time.isoformat(),
                GRAPH_EXECUTION_KEY_FINAL_STATE_KEYS: list((final_state or {}).keys()),
                GRAPH_EXECUTION_KEY_STATE_DELTA_KEYS: list((state_delta or {}).keys()),
                GRAPH_EXECUTION_KEY_ERROR: error,
            },
            visible=True,  # Graph completion is visible
            object_type=GraphEventType.GRAPH_EXECUTION,
        )

    # =========================================================================
    # State Events
    # =========================================================================

    def state_update(
        self,
        updated_keys: list[str],
        removed_keys: Optional[list[str]] = None,
        state_size: int = 0,
    ) -> Event:
        """Create a state update event with structured metadata.

        Args:
            updated_keys: Keys that were updated
            removed_keys: Keys that were removed (optional)
            state_size: Total size of the state

        Returns:
            Event for state update
        """
        # Create structured metadata
        metadata = StateUpdateMetadata(
            updated_keys=updated_keys,
            removed_keys=removed_keys or [],
            state_size=state_size,
        )

        # Store metadata in state_delta
        state_delta: dict[str, Any] = {}
        _store_metadata(state_delta, METADATA_KEY_STATE, metadata)

        text = f"State updated: {len(updated_keys)} keys"
        if removed_keys:
            text += f", {len(removed_keys)} removed"

        return self._build_event(
            text=text,
            state_delta=state_delta,
            partial=True,
            object_type=GraphEventType.GRAPH_STATE_UPDATE,
        )
