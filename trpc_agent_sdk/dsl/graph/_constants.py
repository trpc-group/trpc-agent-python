# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""State key constants and definitions for TRPC-Agent graph module.

This module centralizes all string literal keys used in graph state management.

Usage:
    >>> from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT, STATE_KEY_MESSAGES
    >>> state[STATE_KEY_USER_INPUT] = "Hello"
    >>> messages = state.get(STATE_KEY_MESSAGES, [])
"""

# =============================================================================
# Core State Keys
# =============================================================================

# User input - the current turn's user message
STATE_KEY_USER_INPUT = "user_input"

# Messages - conversation history
STATE_KEY_MESSAGES = "messages"

# Last response - most recent node response (text content)
STATE_KEY_LAST_RESPONSE = "last_response"

# Last response ID - ID for tracking/referencing the last response
STATE_KEY_LAST_RESPONSE_ID = "last_response_id"

# Last tool response - result of the last tool execution
STATE_KEY_LAST_TOOL_RESPONSE = "last_tool_response"

# Node responses - responses keyed by node name
STATE_KEY_NODE_RESPONSES = "node_responses"

# =============================================================================
# One-Shot Message Keys (for three-stage LLM execution)
# =============================================================================

# One-shot messages - consumed after single use (highest priority)
STATE_KEY_ONE_SHOT_MESSAGES = "one_shot_messages"

# One-shot messages by node - node-specific one-shot messages
STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE = "one_shot_messages_by_node"

# =============================================================================
# Metadata and Context Keys
# =============================================================================

# Metadata - TRPC-Agent metadata (invocation_id, branch, session_id, agent_name)
STATE_KEY_METADATA = "metadata"

# Session - reference to the current Session object
STATE_KEY_SESSION = "session"

# Current node ID - ID of the currently executing node
STATE_KEY_CURRENT_NODE_ID = "current_node_id"

# Execution context - execution context for advanced use cases
STATE_KEY_EXEC_CONTEXT = "exec_context"

# =============================================================================
# Callback Keys
# =============================================================================

# Tool callbacks - callbacks for tool execution
STATE_KEY_TOOL_CALLBACKS = "tool_callbacks"

# Model callbacks - callbacks for model execution
STATE_KEY_MODEL_CALLBACKS = "model_callbacks"

# Agent callbacks - callbacks for agent execution
STATE_KEY_AGENT_CALLBACKS = "agent_callbacks"

# Node callbacks - callbacks for node execution
STATE_KEY_NODE_CALLBACKS = "node_callbacks"

# =============================================================================
# Metadata Sub-Keys
# =============================================================================

# Invocation ID within metadata
METADATA_KEY_INVOCATION_ID = "invocation_id"

# Session ID within metadata
METADATA_KEY_SESSION_ID = "session_id"

# Branch within metadata
METADATA_KEY_BRANCH = "branch"

# Agent name within metadata
METADATA_KEY_AGENT_NAME = "agent_name"

# =============================================================================
# Node Type Values
# =============================================================================

NODE_TYPE_FUNCTION = "function"
NODE_TYPE_LLM = "llm"
NODE_TYPE_TOOL = "tool"
NODE_TYPE_AGENT = "agent"
NODE_TYPE_CODE = "code"
NODE_TYPE_KNOWLEDGE = "knowledge"

# =============================================================================
# Graph Boundary Constants
# =============================================================================

# Start and end node identifiers for graph routing
START = "__start__"
END = "__end__"

# =============================================================================
# Step Tracking Keys
# =============================================================================

# Step number - current step in graph execution
STATE_KEY_STEP_NUMBER = "step_number"

# =============================================================================
# Stream/Event Acknowledgement Keys
# =============================================================================

# Custom stream payload key used to carry graph events from nodes
STREAM_KEY_EVENT = "_trpc_graph_event"

# Custom stream payload key used to acknowledge GraphAgent has received an event
STREAM_KEY_ACK = "_trpc_graph_ack"

# =============================================================================
# Internal Checkpoint Storage Keys
# =============================================================================

# LangGraph checkpoint data stored in Session.state
STATE_KEY_CHECKPOINTS = "_trpc_graph_checkpoints"
STATE_KEY_CHECKPOINT_WRITES = "_trpc_graph_checkpoint_writes"
STATE_KEY_CHECKPOINT_BLOBS = "_trpc_graph_checkpoint_blobs"

# =============================================================================
# Internal Interrupt Storage Keys
# =============================================================================

# Pending interrupt marker and metadata stored in Session.state
STATE_KEY_PENDING_INTERRUPT = "_trpc_graph_pending_interrupt"
STATE_KEY_PENDING_INTERRUPT_ID = "_trpc_graph_pending_interrupt_id"
STATE_KEY_PENDING_INTERRUPT_AUTHOR = "_trpc_graph_pending_interrupt_author"
STATE_KEY_PENDING_INTERRUPT_BRANCH = "_trpc_graph_pending_interrupt_branch"

# Prefix for synthetic function call IDs used by graph interrupt bridge
STATE_KEY_LONG_RUNNING_PREFIX = "__trpc_graph_long_running__"

# =============================================================================
# Role Values
# =============================================================================

ROLE_USER = "user"
ROLE_MODEL = "model"
ROLE_FUNCTION = "function"
ROLE_SYSTEM = "system"

# =============================================================================
# Unsafe State Keys (not serializable or should not be exposed)
# =============================================================================

UNSAFE_STATE_KEYS = frozenset({
    STATE_KEY_SESSION,
    STATE_KEY_EXEC_CONTEXT,
    STATE_KEY_CURRENT_NODE_ID,
    STATE_KEY_TOOL_CALLBACKS,
    STATE_KEY_MODEL_CALLBACKS,
    STATE_KEY_AGENT_CALLBACKS,
    STATE_KEY_NODE_CALLBACKS,
    STATE_KEY_CHECKPOINTS,
    STATE_KEY_CHECKPOINT_WRITES,
    STATE_KEY_CHECKPOINT_BLOBS,
    STATE_KEY_PENDING_INTERRUPT,
    STATE_KEY_PENDING_INTERRUPT_ID,
    STATE_KEY_PENDING_INTERRUPT_AUTHOR,
    STATE_KEY_PENDING_INTERRUPT_BRANCH,
})


def is_unsafe_state_key(key: str) -> bool:
    """Check if a state key is unsafe for serialization or exposure.

    Args:
        key: State key to check

    Returns:
        True if the key is unsafe
    """
    return key in UNSAFE_STATE_KEYS
