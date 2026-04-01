# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Structured metadata dataclasses for graph events.

This module provides typed metadata classes that are embedded in Event
objects via the state_delta field.
"""

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import is_dataclass
from typing import Any
from typing import Optional
from typing import TypeVar

from trpc_agent_sdk.events import Event

from ._constants import ExecutionPhase
from ._constants import GRAPH_EXECUTION_KEY_PHASE
from ._constants import METADATA_KEY_MODEL
from ._constants import METADATA_KEY_NODE
from ._constants import METADATA_KEY_TOOL

T = TypeVar("T")


def _extract_event_metadata(event: Event, metadata_key: str, metadata_cls: type[T]) -> Optional[T]:
    """Extract typed metadata from event state_delta."""
    actions = getattr(event, "actions", None)
    state_delta = getattr(actions, "state_delta", None) if actions is not None else None
    if not isinstance(state_delta, dict):
        return None

    metadata_dict = state_delta.get(metadata_key)
    if not isinstance(metadata_dict, dict):
        return None

    try:
        normalized_metadata = dict(metadata_dict)
        phase_value = normalized_metadata.get(GRAPH_EXECUTION_KEY_PHASE)
        if isinstance(phase_value, str):
            normalized_metadata[GRAPH_EXECUTION_KEY_PHASE] = ExecutionPhase(phase_value)
        return metadata_cls(**normalized_metadata)
    except (TypeError, ValueError):
        return None


# =============================================================================
# Structured Metadata Dataclasses
# =============================================================================


@dataclass
class NodeExecutionMetadata:
    """Metadata for node execution events.

    Provides detailed information about node execution including timing
    and input/output tracking.
    """
    node_id: str
    """The unique identifier of the node."""

    node_type: str
    """The type of the node (function, llm, tool, agent, etc.)."""

    phase: ExecutionPhase
    """The execution phase (start, complete, error)."""

    node_description: Optional[str] = None
    """Optional description of the node from NodeConfig."""

    start_time: Optional[str] = None
    """ISO format start time of execution."""

    end_time: Optional[str] = None
    """ISO format end time of execution."""

    duration_ms: float = 0.0
    """Execution duration in milliseconds."""

    step_number: int = 0
    """The execution step number."""

    input_keys: list[str] = field(default_factory=list)
    """Keys of input state."""

    output_keys: list[str] = field(default_factory=list)
    """Keys of output state."""

    error: Optional[str] = None
    """Error message if execution failed."""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    """Tool call information for tool nodes."""

    model_name: Optional[str] = None
    """Model name for LLM nodes."""

    model_input: Optional[str] = None
    """Input sent to LLM nodes."""

    # Retry/cache metadata intentionally omitted in the simplified graph API.

    @classmethod
    def from_event(cls, event: Event) -> Optional["NodeExecutionMetadata"]:
        """Extract NodeExecutionMetadata from an event.

        Args:
            event: Event to extract metadata from

        Returns:
            NodeExecutionMetadata if found, None otherwise
        """
        return _extract_event_metadata(event, METADATA_KEY_NODE, cls)


@dataclass
class ToolExecutionMetadata:
    """Metadata for tool execution events."""

    tool_name: str
    """Name of the tool being executed."""

    tool_id: str
    """Unique identifier of the tool call."""

    node_id: str
    """Node ID where the tool is executed."""

    phase: ExecutionPhase
    """Execution phase (start, complete, error)."""

    start_time: Optional[str] = None
    """ISO format start time."""

    end_time: Optional[str] = None
    """ISO format end time."""

    duration_ms: float = 0.0
    """Execution duration in milliseconds."""

    input_args: Optional[str] = None
    """Tool input arguments (truncated)."""

    output_result: Optional[str] = None
    """Tool output result (truncated)."""

    error: Optional[str] = None
    """Error message if execution failed."""

    @classmethod
    def from_event(cls, event: Event) -> Optional["ToolExecutionMetadata"]:
        """Extract ToolExecutionMetadata from an event.

        Args:
            event: Event to extract metadata from

        Returns:
            ToolExecutionMetadata if found, None otherwise
        """
        return _extract_event_metadata(event, METADATA_KEY_TOOL, cls)


@dataclass
class ModelExecutionMetadata:
    """Metadata for model (LLM) execution events."""

    model_name: str
    """Name of the model being executed."""

    node_id: str
    """Node ID where the model is executed."""

    phase: ExecutionPhase
    """Execution phase (start, complete, error)."""

    start_time: Optional[str] = None
    """ISO format start time."""

    end_time: Optional[str] = None
    """ISO format end time."""

    duration_ms: float = 0.0
    """Execution duration in milliseconds."""

    input_text: Optional[str] = None
    """Model input (messages or prompt, truncated)."""

    output_text: Optional[str] = None
    """Model output result (truncated)."""

    error: Optional[str] = None
    """Error message if execution failed."""

    step_number: int = 0
    """The execution step number."""

    @classmethod
    def from_event(cls, event: Event) -> Optional["ModelExecutionMetadata"]:
        """Extract ModelExecutionMetadata from an event.

        Args:
            event: Event to extract metadata from

        Returns:
            ModelExecutionMetadata if found, None otherwise
        """
        return _extract_event_metadata(event, METADATA_KEY_MODEL, cls)


@dataclass
class StateUpdateMetadata:
    """Metadata for state update events."""

    updated_keys: list[str] = field(default_factory=list)
    """Keys that were updated."""

    removed_keys: list[str] = field(default_factory=list)
    """Keys that were removed."""

    state_size: int = 0
    """Total size of the state."""


@dataclass
class CompletionMetadata:
    """Metadata for graph completion events."""

    total_steps: int
    """Total number of steps executed."""

    total_duration_ms: float
    """Total execution duration in milliseconds."""

    final_state_keys: int
    """Number of keys in the final state."""


# =============================================================================
# Metadata Helper Functions
# =============================================================================


def _store_metadata(state_delta: dict[str, Any], key: str, metadata: Any) -> None:
    """Store metadata in state_delta as JSON."""
    if metadata is not None:
        metadata_dict = asdict(metadata) if is_dataclass(metadata) else metadata
        state_delta[key] = metadata_dict
