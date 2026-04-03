# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Callback system for graph node lifecycle events.

This module provides callback types and the NodeCallbacks class for
registering callbacks that execute during node lifecycle events.

Mirrors the callback system from trpc-agent-go/graph/callbacks.go.
"""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event

# =============================================================================
# Callback Context
# =============================================================================


@dataclass
class NodeCallbackContext:
    """Context passed to node callbacks.

    Contains information about the node being executed and the
    current execution state.

    Attributes:
        node_id: ID of the node being executed
        node_name: Human-readable name of the node
        node_type: Type of node (function, llm, tool, agent)
        step_number: Current step number in graph execution
        execution_start_time: When node execution started
        invocation_id: Current invocation ID
        session_id: Current session ID
        invocation_context: Full invocation context for advanced use cases
    """
    node_id: str
    node_name: str = ""
    node_type: str = "function"
    step_number: int = 0
    execution_start_time: Optional[datetime] = None
    invocation_id: str = ""
    session_id: str = ""
    invocation_context: Optional[InvocationContext] = None

    def __post_init__(self):
        if self.execution_start_time is None:
            self.execution_start_time = datetime.now()
        if not self.node_name:
            self.node_name = self.node_id


# =============================================================================
# Callback Type Definitions
# =============================================================================

# Before node callback: Called before node execution
# Args: (context, state) -> Optional state update or None to continue
BeforeNodeCallback = Callable[[NodeCallbackContext, dict[str, Any]], Awaitable[Optional[dict[str, Any]]]]

# After node callback: Called after successful node execution
# Args: (context, state, result, error) -> Optional modified result
AfterNodeCallback = Callable[[NodeCallbackContext, dict[str, Any], Any, Optional[Exception]], Awaitable[Optional[Any]]]

# On error callback: Called when node execution fails
# Args: (context, state, error) -> None
OnNodeErrorCallback = Callable[[NodeCallbackContext, dict[str, Any], Exception], Awaitable[None]]

# Agent event callback: Called when sub-agent emits an event
# Args: (context, state, event) -> None
# The event parameter is a trpc_agent_sdk.events.Event instance
AgentEventCallback = Callable[[NodeCallbackContext, dict[str, Any], "Event"], Awaitable[None]]

# =============================================================================
# NodeCallbacks Class
# =============================================================================


@dataclass
class NodeCallbacks:
    """Collection of callbacks for node lifecycle events.

    This class holds lists of callbacks that are executed at different
    points during node execution. Callbacks are executed in order.

    Example:
        >>> callbacks = NodeCallbacks()
        >>> callbacks.register_before_node(my_before_callback)
        >>> callbacks.register_after_node(my_after_callback)
        >>> callbacks.register_on_error(my_error_callback)
        >>>
        >>> graph.add_node("my_node", my_action, callbacks=callbacks)
    """
    before_node: list[BeforeNodeCallback] = field(default_factory=list)
    after_node: list[AfterNodeCallback] = field(default_factory=list)
    on_error: list[OnNodeErrorCallback] = field(default_factory=list)
    agent_event: list[AgentEventCallback] = field(default_factory=list)

    def register_before_node(self, callback: BeforeNodeCallback) -> None:
        """Register a callback to run before node execution.

        Args:
            callback: Async function to call before node runs
        """
        self.before_node.append(callback)

    def register_after_node(self, callback: AfterNodeCallback) -> None:
        """Register a callback to run after node execution.

        Args:
            callback: Async function to call after node completes
        """
        self.after_node.append(callback)

    def register_on_error(self, callback: OnNodeErrorCallback) -> None:
        """Register a callback to run when node execution fails.

        Args:
            callback: Async function to call on error
        """
        self.on_error.append(callback)

    def register_agent_event(self, callback: AgentEventCallback) -> None:
        """Register a callback for agent node events.

        Args:
            callback: Async function to call on agent events
        """
        self.agent_event.append(callback)


def merge_callbacks(
    global_callbacks: Optional[NodeCallbacks],
    node_callbacks: Optional[NodeCallbacks],
) -> Optional[NodeCallbacks]:
    """Merge global and per-node callbacks.

    Global callbacks run first for before/error callbacks.
    Per-node callbacks run first for after callbacks (to allow modification).

    Args:
        global_callbacks: Graph-level callbacks
        node_callbacks: Node-specific callbacks

    Returns:
        Merged callbacks or None if both are None
    """
    if global_callbacks is None and node_callbacks is None:
        return None

    if global_callbacks is None:
        return node_callbacks

    if node_callbacks is None:
        return global_callbacks

    merged = NodeCallbacks()

    # Before: global first, then per-node
    merged.before_node = global_callbacks.before_node + node_callbacks.before_node

    # After: per-node first, then global (so per-node can modify)
    merged.after_node = node_callbacks.after_node + global_callbacks.after_node

    # Error: global first, then per-node
    merged.on_error = global_callbacks.on_error + node_callbacks.on_error

    # Agent events: global first, then per-node
    merged.agent_event = global_callbacks.agent_event + node_callbacks.agent_event

    return merged


# =============================================================================
# Convenience Functions
# =============================================================================


def create_logging_callbacks(
    logger: Any = None,
    log_before: bool = True,
    log_after: bool = True,
    log_errors: bool = True,
) -> NodeCallbacks:
    """Create callbacks that log node lifecycle events.

    Args:
        logger: Logger instance (uses print if None)
        log_before: Whether to log before node execution
        log_after: Whether to log after node execution
        log_errors: Whether to log errors

    Returns:
        NodeCallbacks with logging functions
    """

    def log(msg: str):
        if logger:
            logger.info(msg)
        else:
            print(msg)

    callbacks = NodeCallbacks()

    if log_before:

        async def before_log(ctx: NodeCallbackContext, state: dict) -> None:
            log(f"[{ctx.node_id}] Starting node execution")
            return None

        callbacks.register_before_node(before_log)

    if log_after:

        async def after_log(ctx: NodeCallbackContext, state: dict, result: Any, error: Optional[Exception]) -> None:
            duration = (datetime.now() - ctx.execution_start_time).total_seconds() if ctx.execution_start_time else 0
            log(f"[{ctx.node_id}] Completed in {duration:.3f}s")
            return None

        callbacks.register_after_node(after_log)

    if log_errors:

        async def error_log(ctx: NodeCallbackContext, state: dict, error: Exception) -> None:
            log(f"[{ctx.node_id}] Error: {error}")

        callbacks.register_on_error(error_log)

    return callbacks
