# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TRPC-Agent Graph Module.

User-facing API for building graph-based agent workflows.
Uses LangGraph as the underlying execution engine.

This module provides a minimal, focused API for building graph-based workflows.
For event utilities, import from:
    - trpc_agent_dsl.graph.events - Event building and inspection (EventBuilder, EventUtils)

Example with NodeConfig pattern (recommended):
    >>> from trpc_agent_sdk.dsl.graph import (
    ...     State,
    ...     StateGraph,
    ...     GraphAgent,
    ...     NodeConfig,
    ...     STATE_KEY_USER_INPUT,
    ...     START,
    ...     END,
    ... )
    >>>
    >>> class MyState(State):
    ...     result: str
    >>>
    >>> config = NodeConfig(
    ...     name="Greeter",
    ...     description="Greets the user"
    ... )
    >>>
    >>> async def greet(state: MyState) -> dict:
    ...     return {"result": f"Hello, {state.get(STATE_KEY_USER_INPUT, 'world')}!"}
    >>>
    >>> graph = StateGraph(MyState)
    >>> graph.add_node("greet", greet, config=config)
    >>> graph.add_edge(START, "greet")
    >>> graph.add_edge("greet", END)
    >>>
    >>> agent = GraphAgent(
    ...     name="greeter",
    ...     graph=graph.compile()
    ... )

Node Signatures:
    Nodes support signatures for different use cases:

    1. Simple: async def node(state: State) -> dict
       - For computations that don't need streaming

    2. Streaming (sync): async def node(state: State, writer: EventWriter) -> dict
       - For operations that emit partial results without awaits

    3. Streaming (async): async def node(state: State, async_writer: AsyncEventWriter) -> dict
       - For operations that want to await event writes

    4. Context: async def node(state: State, ctx: InvocationContext) -> dict
       - For operations needing session info or full context

    5. Context + streaming also supported using either writer parameter.
       You may also request both `writer` and `async_writer` in one node.

Thread Safety:
    Context is passed via config["configurable"]["invocation_context"]
    for thread-safe concurrent execution. The graph instance is immutable
    and can be safely reused across concurrent invocations.
"""

# =============================================================================
# Core API - Main graph building classes
# =============================================================================
from ._callbacks import NodeCallbackContext
from ._callbacks import NodeCallbacks
from ._callbacks import create_logging_callbacks
from ._callbacks import merge_callbacks
from ._constants import END
from ._constants import START
from ._constants import STATE_KEY_AGENT_CALLBACKS
from ._constants import STATE_KEY_CURRENT_NODE_ID
from ._constants import STATE_KEY_EXEC_CONTEXT
from ._constants import STATE_KEY_LAST_RESPONSE
from ._constants import STATE_KEY_LAST_RESPONSE_ID
from ._constants import STATE_KEY_LAST_TOOL_RESPONSE
from ._constants import STATE_KEY_MESSAGES
from ._constants import STATE_KEY_METADATA
from ._constants import STATE_KEY_MODEL_CALLBACKS
from ._constants import STATE_KEY_NODE_CALLBACKS
from ._constants import STATE_KEY_NODE_RESPONSES
from ._constants import STATE_KEY_ONE_SHOT_MESSAGES
from ._constants import STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE
from ._constants import STATE_KEY_SESSION
from ._constants import STATE_KEY_STEP_NUMBER
from ._constants import STATE_KEY_TOOL_CALLBACKS
from ._constants import STATE_KEY_USER_INPUT
from ._constants import is_unsafe_state_key
from ._event_writer import AsyncEventWriter
from ._event_writer import EventWriter
from ._event_writer import EventWriterBase
from ._graph_agent import GraphAgent
from ._interrupt import interrupt
from ._memory_saver import MemorySaver
from ._memory_saver import MemorySaverOption
from ._memory_saver import has_graph_internal_checkpoint_state
from ._memory_saver import strip_graph_internal_checkpoint_state
from ._node_config import NodeConfig
from ._state import State
from ._state import StateUtils
from ._state import append_list
from ._state import merge_dict
from ._state import messages_reducer
from ._state_graph import CompiledStateGraph
from ._state_graph import StateGraph
from ._events import EventUtils
from ._events import ExecutionPhase
from ._events import ModelExecutionMetadata
from ._events import NodeExecutionMetadata
from ._events import ToolExecutionMetadata
from ._state_mapper import StateMapper
from ._state_mapper import SubgraphResult

__all__ = [
    "NodeCallbacks",
    "END",
    "START",
    "STATE_KEY_LAST_RESPONSE",
    "STATE_KEY_LAST_RESPONSE_ID",
    "STATE_KEY_LAST_TOOL_RESPONSE",
    "STATE_KEY_MESSAGES",
    "STATE_KEY_NODE_RESPONSES",
    "STATE_KEY_USER_INPUT",
    "STATE_KEY_AGENT_CALLBACKS",
    "STATE_KEY_CURRENT_NODE_ID",
    "STATE_KEY_EXEC_CONTEXT",
    "STATE_KEY_METADATA",
    "STATE_KEY_MODEL_CALLBACKS",
    "STATE_KEY_NODE_CALLBACKS",
    "STATE_KEY_ONE_SHOT_MESSAGES",
    "STATE_KEY_ONE_SHOT_MESSAGES_BY_NODE",
    "STATE_KEY_SESSION",
    "STATE_KEY_STEP_NUMBER",
    "STATE_KEY_TOOL_CALLBACKS",
    "is_unsafe_state_key",
    "AsyncEventWriter",
    "EventWriter",
    "EventWriterBase",
    "GraphAgent",
    "interrupt",
    "MemorySaver",
    "MemorySaverOption",
    "has_graph_internal_checkpoint_state",
    "strip_graph_internal_checkpoint_state",
    "NodeConfig",
    "State",
    "StateUtils",
    "append_list",
    "merge_dict",
    "messages_reducer",
    "CompiledStateGraph",
    "StateGraph",
    "StateMapper",
    "SubgraphResult",
    "NodeCallbackContext",
    "create_logging_callbacks",
    "merge_callbacks",
    "EventUtils",
    "ExecutionPhase",
    "ModelExecutionMetadata",
    "NodeExecutionMetadata",
    "ToolExecutionMetadata",
]
