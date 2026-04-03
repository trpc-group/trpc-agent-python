# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""State class and reducers for TRPC-Agent graph execution.

This module defines the base State class that flows through graph nodes.
Unlike LangGraph's MessagesState, this uses a custom messages reducer
that handles google.genai.types.Content objects directly.
"""

from copy import deepcopy
from typing import Annotated
from typing import Any
from typing import Optional
from typing import TypeVar
from typing_extensions import TypedDict

from google.genai.types import Content

from trpc_agent_sdk.agents._callback import AgentCallback
from trpc_agent_sdk.agents._callback import ModelCallback
from trpc_agent_sdk.agents._callback import ToolCallback
from trpc_agent_sdk.sessions import Session

from ._callbacks import NodeCallbacks
from ._constants import METADATA_KEY_INVOCATION_ID
from ._constants import METADATA_KEY_SESSION_ID
from ._constants import STATE_KEY_LAST_RESPONSE
from ._constants import STATE_KEY_LAST_RESPONSE_ID
from ._constants import STATE_KEY_LAST_TOOL_RESPONSE
from ._constants import STATE_KEY_MESSAGES
from ._constants import STATE_KEY_METADATA
from ._constants import STATE_KEY_NODE_RESPONSES
from ._constants import STATE_KEY_ONE_SHOT_MESSAGES
from ._constants import STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
from ._constants import STATE_KEY_SESSION
from ._constants import STATE_KEY_STEP_NUMBER
from ._constants import STATE_KEY_USER_INPUT

# =============================================================================
# Reducer Functions
# =============================================================================


def merge_dict(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer that merges dictionaries.

    New keys override existing keys (shallow merge).

    Args:
        existing: Current dictionary
        new: Dictionary with updates

    Returns:
        Merged dictionary
    """
    if existing is None:
        existing = {}
    if new is None:
        return existing
    result = dict(existing)
    result.update(new)
    return result


def append_list(existing: list[Any] | None, new: list[Any] | Any | None) -> list[Any]:
    """Reducer that appends to lists.

    Args:
        existing: Current list
        new: Item(s) to append

    Returns:
        Extended list
    """
    if existing is None:
        existing = []
    if new is None:
        return existing
    if isinstance(new, list):
        return existing + new
    return existing + [new]


def messages_reducer(existing: list[Content] | None, new: list[Content] | Content | None) -> list[Content]:
    """Reducer for messages that handles google.genai.types.Content objects.

    This is similar to LangGraph's add_messages reducer but works with
    google.genai.types.Content objects instead of LangChain BaseMessage.

    Mirrors the MessageReducer from trpc-agent-go/graph/state.go.

    Args:
        existing: Current list of messages (Content objects)
        new: New message(s) to append

    Returns:
        Extended list of messages
    """
    if existing is None:
        existing = []
    if new is None:
        return existing
    if isinstance(new, list):
        return existing + new
    return existing + [new]


def _step_number_reducer(existing: int | None, new: int | None) -> int:
    """Reducer for step_number that resolves concurrent writes safely.

    LangGraph may execute multiple nodes in the same super-step. When those
    nodes all return STATE_KEY_STEP_NUMBER, use the largest observed value.
    """
    if existing is None:
        existing = 0
    if new is None:
        return existing
    return max(existing, new)


# =============================================================================
# State Class
# =============================================================================


class State(TypedDict, total=False):
    """Base state for TRPC-Agent graph execution.

    Uses a custom messages reducer that handles google.genai.types.Content
    objects directly, rather than LangChain's BaseMessage types.

    Users should extend this class to add custom state fields.
    All built-in fields are optional (total=False).

    Built-in Fields:
        STATE_KEY_MESSAGES: Chat history with custom reducer (handles Content objects)
        STATE_KEY_USER_INPUT: Last user input text
        STATE_KEY_LAST_RESPONSE: Most recent node response (text content)
        STATE_KEY_LAST_RESPONSE_ID: ID of the last response (for tracking/referencing)
        STATE_KEY_LAST_TOOL_RESPONSE: Result of the last tool execution
        STATE_KEY_NODE_RESPONSES: Responses keyed by node name
        STATE_KEY_ONE_SHOT_MESSAGES: Messages to include only once in next LLM call
        STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE: Node-specific one-shot messages
        STATE_KEY_METADATA: TRPC-Agent metadata (invocation_id, branch, session_id, agent_name)
        STATE_KEY_SESSION: Reference to the current Session object
        STATE_KEY_CURRENT_NODE_ID: ID of the currently executing node
        STATE_KEY_EXEC_CONTEXT: Execution context for advanced use cases
        STATE_KEY_TOOL_CALLBACKS: Callbacks for tool execution
        STATE_KEY_MODEL_CALLBACKS: Callbacks for model execution
        STATE_KEY_AGENT_CALLBACKS: Callbacks for agent execution
        STATE_KEY_NODE_CALLBACKS: Callbacks for node execution

    Three-Stage LLM Execution Rule:
        The graph module implements a three-stage message selection rule:
        1. STATE_KEY_ONE_SHOT_MESSAGES: Consumed after single use (highest priority)
        2. STATE_KEY_USER_INPUT: Current turn's user input (if not empty)
        3. STATE_KEY_MESSAGES: Full conversation history (fallback)

        This is controlled by STATE_KEY_ONE_SHOT_MESSAGES and STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE.

    Example:
        >>> class MyState(State):
        ...     counter: int
        ...     search_results: list[str]
        ...     classification: str
    """
    # Core fields - STATE_KEY_MESSAGES now uses custom reducer instead of LangGraph's add_messages
    messages: Annotated[list[Content], messages_reducer]
    user_input: str
    last_response: Optional[str]
    last_response_id: Optional[str]  # ID for tracking the last response
    last_tool_response: Optional[str]  # Result of last tool execution

    # Node output tracking
    node_responses: Annotated[dict[str, Any], merge_dict]

    # One-shot messages (for three-stage LLM execution)
    # These messages are consumed after single use
    one_shot_messages: Annotated[list[Content], append_list]
    one_shot_messages_by_node: Annotated[dict[str, list[Content]], merge_dict]

    # Metadata
    metadata: Annotated[dict[str, Any], merge_dict]

    # Session and context
    session: Optional[Session]  # Session object reference
    current_node_id: str
    exec_context: Any  # Execution context

    # Callbacks (stored in state for node access)
    tool_callbacks: Optional[ToolCallback]
    model_callbacks: Optional[ModelCallback]
    agent_callbacks: Optional[AgentCallback]
    node_callbacks: Optional[NodeCallbacks]

    # Step tracking
    step_number: Annotated[int, _step_number_reducer]  # Current step in graph execution


# =============================================================================
# State Utility Class
# =============================================================================

T = TypeVar('T', bound=dict)


class StateUtils:
    """Utility methods for working with graph state."""

    @staticmethod
    def clone(state: T) -> T:
        """Create a deep copy of the state.

        Args:
            state: State dictionary to clone

        Returns:
            Deep copy of the state
        """
        return deepcopy(state)

    @staticmethod
    def get_user_input(state: dict[str, Any]) -> str:
        """Get user input from state.

        Args:
            state: State dictionary

        Returns:
            User input string or empty string
        """
        return state.get(STATE_KEY_USER_INPUT, "")

    @staticmethod
    def get_last_response(state: dict[str, Any]) -> str:
        """Get last response from state.

        Args:
            state: State dictionary

        Returns:
            Last response string or empty string
        """
        return state.get(STATE_KEY_LAST_RESPONSE, "")

    @staticmethod
    def get_node_response(state: dict[str, Any], node_id: str) -> Any:
        """Get response from a specific node.

        Args:
            state: State dictionary
            node_id: ID of the node

        Returns:
            Node response or None
        """
        responses = state.get(STATE_KEY_NODE_RESPONSES, {})
        return responses.get(node_id)

    @staticmethod
    def get_metadata(state: dict[str, Any]) -> dict[str, Any]:
        """Get metadata from state.

        Args:
            state: State dictionary

        Returns:
            Metadata dictionary
        """
        return state.get(STATE_KEY_METADATA, {})

    @staticmethod
    def get_invocation_id(state: dict[str, Any]) -> str:
        """Get invocation ID from state metadata.

        Args:
            state: State dictionary

        Returns:
            Invocation ID or empty string
        """
        metadata = StateUtils.get_metadata(state)
        return metadata.get(METADATA_KEY_INVOCATION_ID, "")

    @staticmethod
    def get_session_id(state: dict[str, Any]) -> str:
        """Get session ID from state metadata.

        Args:
            state: State dictionary

        Returns:
            Session ID or empty string
        """
        metadata = StateUtils.get_metadata(state)
        return metadata.get(METADATA_KEY_SESSION_ID, "")

    @staticmethod
    def get_messages(state: dict[str, Any]) -> list[Content]:
        """Get messages from state.

        Args:
            state: State dictionary

        Returns:
            List of messages (Content from genai)
        """
        return state.get(STATE_KEY_MESSAGES, [])

    @staticmethod
    def get_session(state: dict[str, Any]) -> Any:
        """Get session from state.

        Args:
            state: State dictionary

        Returns:
            Session object or None
        """
        return state.get(STATE_KEY_SESSION)

    @staticmethod
    def get_last_response_id(state: dict[str, Any]) -> str:
        """Get last response ID from state.

        Args:
            state: State dictionary

        Returns:
            Last response ID or empty string
        """
        return state.get(STATE_KEY_LAST_RESPONSE_ID, "")

    @staticmethod
    def get_last_tool_response(state: dict[str, Any]) -> str:
        """Get last tool response from state.

        Args:
            state: State dictionary

        Returns:
            Last tool response or empty string
        """
        return state.get(STATE_KEY_LAST_TOOL_RESPONSE, "")

    @staticmethod
    def get_one_shot_messages(state: dict[str, Any]) -> list[Content]:
        """Get one-shot messages from state.

        One-shot messages are consumed after single use in LLM execution.

        Args:
            state: State dictionary

        Returns:
            List of one-shot messages
        """
        return state.get(STATE_KEY_ONE_SHOT_MESSAGES, [])

    @staticmethod
    def get_one_shot_messages_for_node(state: dict[str, Any], node_id: str) -> list[Content]:
        """Get one-shot messages for a specific node.

        Args:
            state: State dictionary
            node_id: ID of the node

        Returns:
            List of one-shot messages for the node
        """
        by_node = state.get(STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE, {})
        return by_node.get(node_id, [])

    @staticmethod
    def consume_one_shot_messages(state: dict[str, Any], node_id: str) -> tuple[list[Content], dict[str, Any]]:
        """Consume and return one-shot messages for a node.

        This retrieves global one-shot messages plus node-specific ones,
        and returns a state update that clears them.

        Args:
            state: State dictionary
            node_id: ID of the node consuming the messages

        Returns:
            Tuple of (messages, state_update) where state_update clears consumed messages
        """
        global_msgs = state.get(STATE_KEY_ONE_SHOT_MESSAGES, [])
        by_node = state.get(STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE, {})
        node_msgs = by_node.get(node_id, [])

        # Combine messages
        all_msgs = list(global_msgs) + list(node_msgs)

        # Build state update to clear consumed messages
        state_update: dict[str, Any] = {}
        if global_msgs:
            state_update[STATE_KEY_ONE_SHOT_MESSAGES] = []
        if node_msgs:
            # Remove this node's messages
            updated_by_node = dict(by_node)
            updated_by_node.pop(node_id, None)
            state_update[STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE] = updated_by_node

        return all_msgs, state_update

    @staticmethod
    def get_step_number(state: dict[str, Any]) -> int:
        """Get current step number from state.

        Args:
            state: State dictionary

        Returns:
            Current step number (0 if not set)
        """
        return state.get(STATE_KEY_STEP_NUMBER, 0)
