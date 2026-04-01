# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Event constants and enums for graph execution events.

This module contains all constants used for metadata keys, object types,
and execution phase enumerations for graph events.
"""

from enum import Enum

# =============================================================================
# Metadata Key Constants (for StateDelta storage)
# =============================================================================

METADATA_KEY_NODE = "_node_metadata"
"""Metadata key for node execution metadata."""

METADATA_KEY_TOOL = "_tool_metadata"
"""Metadata key for tool execution metadata."""

METADATA_KEY_MODEL = "_model_metadata"
"""Metadata key for model execution metadata."""

METADATA_KEY_STATE = "_state_metadata"
"""Metadata key for state update metadata."""

# =============================================================================
# Graph Execution State Delta Keys
# =============================================================================

GRAPH_EXECUTION_KEY_PHASE = "phase"
"""State delta key for graph execution phase."""

GRAPH_EXECUTION_KEY_TOTAL_STEPS = "total_steps"
"""State delta key for graph execution total steps."""

GRAPH_EXECUTION_KEY_TOTAL_DURATION_MS = "total_duration_ms"
"""State delta key for graph execution total duration in milliseconds."""

GRAPH_EXECUTION_KEY_START_TIME = "start_time"
"""State delta key for graph execution start time."""

GRAPH_EXECUTION_KEY_END_TIME = "end_time"
"""State delta key for graph execution end time."""

GRAPH_EXECUTION_KEY_FINAL_STATE_KEYS = "final_state_keys"
"""State delta key listing keys included in final state."""

GRAPH_EXECUTION_KEY_STATE_DELTA_KEYS = "state_delta_keys"
"""State delta key listing keys included in state delta."""

GRAPH_EXECUTION_KEY_ERROR = "error"
"""State delta key for graph execution error message."""

# =============================================================================
# Event Object Types (for Event.object field)
# =============================================================================


class GraphEventType(str, Enum):
    """Graph event object type identifiers."""

    GRAPH_EXECUTION = "graph.execution"
    GRAPH_NODE_EXECUTION = "graph.node.execution"
    GRAPH_NODE_START = "graph.node.start"
    GRAPH_NODE_COMPLETE = "graph.node.complete"
    GRAPH_NODE_ERROR = "graph.node.error"
    GRAPH_STATE_UPDATE = "graph.state.update"


class ExecutionPhase(str, Enum):
    """Execution phase identifiers."""
    START = "start"
    COMPLETE = "complete"
    ERROR = "error"
