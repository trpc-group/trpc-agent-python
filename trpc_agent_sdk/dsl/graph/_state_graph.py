# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""StateGraph wrapper for TRPC-Agent integration.

This module provides StateGraph and CompiledStateGraph classes built on top
of LangGraph with TRPC-Agent-specific features.
"""

import inspect
from typing import Any
from typing import Callable
from typing import Hashable
from typing import Optional
from typing import Type
from typing import Union

from langgraph.config import get_config
from langgraph.config import get_stream_writer
from langgraph.errors import GraphInterrupt
from langgraph.graph import StateGraph as LangGraphStateGraph
from langgraph.types import Command

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models._llm_model import LLMModel
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.types import GenerateContentConfig

from ._callbacks import NodeCallbackContext
from ._callbacks import NodeCallbacks
from ._callbacks import merge_callbacks
from ._constants import END
from ._constants import NODE_TYPE_AGENT
from ._constants import NODE_TYPE_CODE
from ._constants import NODE_TYPE_FUNCTION
from ._constants import NODE_TYPE_KNOWLEDGE
from ._constants import NODE_TYPE_LLM
from ._constants import NODE_TYPE_TOOL
from ._constants import START
from ._constants import STATE_KEY_METADATA
from ._constants import STATE_KEY_STEP_NUMBER
from ._event_writer import AsyncEventWriter
from ._event_writer import EventWriter
from ._memory_saver import MemorySaver
from ._memory_saver import MemorySaverOption
from ._node_action import AgentNodeAction
from ._node_action import CodeNodeAction
from ._node_action import KnowledgeNodeAction
from ._node_action import LLMNodeAction
from ._node_action import MCPNodeAction
from ._node_config import NodeConfig
from ._state import State
from ._state_mapper import SubgraphResult

# Special node identifiers for graph routing
START_NODE = START
END_NODE = END


class StateGraph:
    """Wrapper around LangGraph's StateGraph with TRPC-Agent integration.

    Provides a simplified API for building agent workflows with automatic
    event emission and context injection.

    Supports node signatures:
        1. (state) - Simple node, no streaming or context needed
        2. (state, writer) - Node that streams partial results (sync writer)
        3. (state, async_writer) - Node that streams partial results (async writer)
        4. (state, ctx) - Node that needs full invocation context
        5. (state, writer, ctx) - Node that needs both (sync writer)
        6. (state, async_writer, ctx) - Node that needs both (async writer)
        7. (state, writer, async_writer) - Node that uses both writer types
        8. (state, writer, async_writer, ctx) - Node that uses both writers and context

    Example:
        >>> graph = StateGraph(MyState)
        >>> graph.add_node("processor", process_data)
        >>> graph.add_node("responder", generate_response)
        >>> graph.add_edge(START, "processor")
        >>> graph.add_edge("processor", "responder")
        >>> graph.add_edge("responder", END)
        >>> compiled = graph.compile()
    """

    def __init__(
        self,
        state_schema: Type[State],
        *,
        callbacks: Optional[NodeCallbacks] = None,
    ):
        """Initialize the StateGraph.

        Args:
            state_schema: TypedDict class defining the state structure
            callbacks: Global callbacks applied to all nodes (optional)
        """
        self._graph = LangGraphStateGraph(state_schema)
        self._node_configs: dict[str, NodeConfig] = {}  # Node configurations
        self._node_callbacks: Optional[NodeCallbacks] = callbacks  # Global callbacks
        self._agent_nodes: dict[str, BaseAgent] = {}  # Agent instances added via add_agent_node

    def add_node(
        self,
        name: str,
        action: Callable,
        *,
        config: Optional[NodeConfig] = None,
        callbacks: Optional[NodeCallbacks] = None,
        _node_type: str = NODE_TYPE_FUNCTION,
    ) -> "StateGraph":
        """Add a node to the graph.

        The action function is automatically wrapped to:
        1. Inject EventWriter/AsyncEventWriter or InvocationContext based on signature
        2. Handle streaming via LangGraph's custom stream mode

        Supported signatures:
            - async def node(state: State) -> dict
            - async def node(state: State, writer: EventWriter) -> dict
            - async def node(state: State, async_writer: AsyncEventWriter) -> dict
            - async def node(state: State, ctx: InvocationContext) -> dict
            - async def node(state: State, writer: EventWriter, ctx: InvocationContext) -> dict
            - async def node(state: State, async_writer: AsyncEventWriter, ctx: InvocationContext) -> dict
            - async def node(state: State, writer: EventWriter, async_writer: AsyncEventWriter) -> dict
            - async def node(state: State, writer: EventWriter, async_writer: AsyncEventWriter,
                             ctx: InvocationContext) -> dict

        Args:
            name: Unique name for the node.
            action: Async function implementing node logic.
            config: Common NodeConfig for this node (optional).
            callbacks: Lifecycle callbacks for this node (optional).
            _node_type: Internal node type value for metadata/events.

        Returns:
            Self for method chaining

        Raises:
            TypeError: If action is not an async function

        Example:
            >>> from trpc_agent_sdk.dsl.graph import NodeConfig
            >>> config = NodeConfig(name="Processor", description="Processes data")
            >>> graph.add_node("process", process_action, config=config)
        """
        if not inspect.iscoroutinefunction(action):
            raise TypeError(f"Node action '{name}' must be async")

        # Create default config if not provided
        if config is None:
            config = NodeConfig(name=name)
        elif config.name is None:
            config.name = name

        # Store config
        self._node_configs[name] = config

        # Create wrapper
        wrapper = self._create_node_wrapper(name, action, config, _node_type, callbacks)

        metadata = config.to_metadata(node_type=_node_type)

        if metadata:
            self._graph.add_node(name, wrapper, metadata=metadata or None)
        else:
            self._graph.add_node(name, wrapper)

        return self

    def _create_node_wrapper(
        self,
        name: str,
        action: Callable,
        config: NodeConfig,
        node_type: str,
        node_callbacks: Optional[NodeCallbacks],
    ) -> Callable:
        """Create a wrapper function for a node action.

        Args:
            name: Node name
            action: Original action function
            config: Node configuration
            node_type: Node type string for emitted metadata/events
            node_callbacks: Node-level callbacks passed via add_node/add_*_node

        Returns:
            Wrapped async function
        """
        # Inspect signature to determine what to inject
        sig = inspect.signature(action)
        params = list(sig.parameters.keys())
        needs_writer = "writer" in params
        needs_async_writer = "async_writer" in params
        needs_ctx = "ctx" in params
        needs_callback_ctx = "callback_ctx" in params
        needs_callbacks = "callbacks" in params

        # Reference for closure
        graph_ref = self

        async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
            """Wrapper that injects dependencies based on node signature."""
            stream_writer = get_stream_writer()

            # Get invocation context from runtime config (thread-safe)
            runnable_config = get_config()
            configurable = runnable_config.get("configurable", {})
            ctx: Optional[InvocationContext] = configurable.get("invocation_context")

            # Get metadata from state for event construction
            metadata_dict = state.get(STATE_KEY_METADATA, {})
            invocation_id = metadata_dict.get("invocation_id", "")
            branch = metadata_dict.get("branch", "")
            session_id = metadata_dict.get("session_id", "")

            # Get and increment step number
            current_step = state.get(STATE_KEY_STEP_NUMBER, 0)

            # Create writers for streaming
            writer = EventWriter(
                writer=stream_writer,
                invocation_id=invocation_id,
                author=name,
                branch=branch,
            )
            async_writer = AsyncEventWriter(
                writer=stream_writer,
                invocation_id=invocation_id,
                author=name,
                branch=branch,
            )

            # Create callback context with actual step number
            callback_ctx = NodeCallbackContext(
                node_id=name,
                node_name=config.name or name,
                node_type=node_type,
                step_number=current_step,
                invocation_id=invocation_id,
                session_id=session_id,
                invocation_context=ctx,
            )

            # Merge global and node-specific callbacks
            callbacks = merge_callbacks(graph_ref._node_callbacks, node_callbacks)

            # Run before callbacks if any
            if callbacks and callbacks.before_node:
                for callback in callbacks.before_node:
                    result = await callback(callback_ctx, state)
                    if result is not None:
                        return result

            # Emit node start event
            await async_writer.write_node_start(
                node_id=name,
                node_type=node_type,
                node_description=config.description,
                step_number=current_step,
                input_keys=list(state.keys()),
            )

            try:
                # Call original action with appropriate arguments
                if needs_ctx and ctx is None:
                    raise RuntimeError(
                        f"Node '{name}' requires InvocationContext but none was set. "
                        "Pass context via config['configurable']['invocation_context'] when executing the graph.")

                kwargs: dict[str, Any] = {}
                if needs_writer:
                    kwargs["writer"] = writer
                if needs_async_writer:
                    kwargs["async_writer"] = async_writer
                if needs_ctx:
                    kwargs["ctx"] = ctx
                if needs_callback_ctx:
                    kwargs["callback_ctx"] = callback_ctx
                if needs_callbacks:
                    kwargs["callbacks"] = callbacks

                if kwargs:
                    result = await action(state, **kwargs)
                else:
                    result = await action(state)

                if result is None:
                    state_update = {}
                elif isinstance(result, dict):
                    state_update = result
                else:
                    raise TypeError(f"Node '{name}' must return a dict or None, got {type(result).__name__}")

                # Increment step number in state update
                state_update[STATE_KEY_STEP_NUMBER] = current_step + 1

                # Emit node complete event
                await async_writer.write_node_complete(
                    node_id=name,
                    node_type=node_type,
                    node_description=config.description,
                    step_number=current_step,
                    output_keys=list(state_update.keys()) if state_update else [],
                )

                # Run after callbacks if any
                if callbacks and callbacks.after_node:
                    for callback in callbacks.after_node:
                        modified = await callback(callback_ctx, state, state_update, None)
                        if modified is not None:
                            state_update = modified

                return state_update

            except GraphInterrupt:
                # Interrupt is control flow for pause/resume, not node failure.
                raise
            except Exception as e:
                # Emit node error event
                await async_writer.write_node_error(
                    node_id=name,
                    error=str(e),
                    node_type=node_type,
                    node_description=config.description,
                    step_number=current_step,
                )

                # Run error callbacks if any
                if callbacks and callbacks.on_error:
                    for callback in callbacks.on_error:
                        await callback(callback_ctx, state, e)
                raise

        return wrapper

    def add_llm_node(
        self,
        name: str,
        model: LLMModel,
        instruction: str,
        *,
        tools: Optional[dict[str, Any]] = None,
        tool_parallel: bool = False,
        max_tool_iterations: int = 8,
        generation_config: Optional[GenerateContentConfig] = None,
        config: Optional[NodeConfig] = None,
        callbacks: Optional[NodeCallbacks] = None,
    ) -> "StateGraph":
        """Add an LLM node that uses a model directly.

        This creates a node that:
        1. Builds messages from state
        2. Calls the model with instruction and tools
        3. Emits streaming events
        4. Updates state with response

        Args:
            name: Unique name for the node
            model: LLM model instance (from trpc_agent_sdk.models, e.g., OpenAIModel)
            instruction: System instruction for the model
            tools: Optional dict of tools available to the model. When provided,
                tool calls are executed in-node and fed back to the model until
                no function_call remains or max_tool_iterations is reached.
            tool_parallel: Whether multiple tool calls in the same round run in parallel
            max_tool_iterations: Maximum model->tool loop rounds per invocation
            generation_config: Optional generation configuration (temperature, max_tokens, etc.)
            config: Common NodeConfig for the node (optional)
            callbacks: Lifecycle callbacks for this node (optional)

        Returns:
            Self for method chaining

        Example:
            >>> from trpc_agent_sdk.models import OpenAIModel
            >>> from trpc_agent_sdk.types import GenerateContentConfig
            >>> from trpc_agent_sdk.dsl.graph import NodeConfig
            >>> model = OpenAIModel(model_name="deepseek-v3", api_key="...", base_url="...")
            >>> gen_config = GenerateContentConfig(temperature=0.7, max_output_tokens=1000)
            >>> config = NodeConfig(name="Classifier", description="Classifies intent")
            >>> graph.add_llm_node("classify", model, "Classify user intent",
            ...                     generation_config=gen_config, config=config)
        """
        if max_tool_iterations <= 0:
            raise ValueError("max_tool_iterations must be greater than 0")

        if config is None:
            config = NodeConfig(name=name)
        elif config.name is None:
            config.name = name

        async def llm_action(
            state: State,
            writer: EventWriter,
            async_writer: AsyncEventWriter,
            ctx: Optional[InvocationContext] = None,
        ) -> dict:
            action = LLMNodeAction(
                name,
                model,
                instruction,
                tools or {},
                tool_parallel=tool_parallel,
                max_tool_iterations=max_tool_iterations,
                generation_config=generation_config,
                writer=writer,
                async_writer=async_writer,
                ctx=ctx,
            )
            return await action.execute(state)

        return self.add_node(
            name,
            llm_action,
            config=config,
            callbacks=callbacks,
            _node_type=NODE_TYPE_LLM,
        )

    def add_code_node(
        self,
        name: str,
        code_executor: BaseCodeExecutor,
        code: str,
        language: str,
        *,
        config: Optional[NodeConfig] = None,
        callbacks: Optional[NodeCallbacks] = None,
    ) -> "StateGraph":
        """Add a code execution node.

        This creates a node that executes static code via code executor and
        writes result text into built-in state keys.
        Access per-node output via STATE_KEY_NODE_RESPONSES[name].

        Args:
            name: Unique name for the node.
            code_executor: Pre-configured BaseCodeExecutor instance
                (e.g. UnsafeLocalCodeExecutor, ContainerCodeExecutor).
            code: Source code to execute.
            language: Code language (python/bash/sh).
            config: Optional NodeConfig.
            callbacks: Optional node callbacks.

        Returns:
            Self for method chaining.
        """

        async def code_action(
            state: State,
            writer: EventWriter,
            async_writer: AsyncEventWriter,
            ctx: Optional[InvocationContext] = None,
        ) -> dict[str, Any]:
            action = CodeNodeAction(
                name=name,
                code_executor=code_executor,
                code=code,
                language=language,
                writer=writer,
                async_writer=async_writer,
                ctx=ctx,
            )
            return await action.execute(state)

        return self.add_node(
            name,
            code_action,
            config=config,
            callbacks=callbacks,
            _node_type=NODE_TYPE_CODE,
        )

    def add_knowledge_node(
        self,
        name: str,
        query: Union[str, Callable[[State], str]],
        tool: LangchainKnowledgeSearchTool,
        *,
        config: Optional[NodeConfig] = None,
        callbacks: Optional[NodeCallbacks] = None,
    ) -> "StateGraph":
        """Add a standalone knowledge-search node.

        Args:
            name: Unique name for the node.
            query: Search query string or callable resolving query from state.
            tool: Prebuilt LangchainKnowledgeSearchTool instance.
            config: Optional NodeConfig.
            callbacks: Optional node callbacks.
        """
        if tool is None:
            raise TypeError(f"Knowledge tool for node '{name}' must not be None.")

        if config is None:
            config = NodeConfig(name=name)
        elif config.name is None:
            config.name = name

        async def knowledge_action(
            state: State,
            writer: EventWriter,
            async_writer: AsyncEventWriter,
            ctx: Optional[InvocationContext] = None,
        ) -> dict[str, Any]:
            action = KnowledgeNodeAction(
                name=name,
                query=query,
                tool=tool,
                writer=writer,
                async_writer=async_writer,
                ctx=ctx,
            )
            return await action.execute(state)

        return self.add_node(
            name,
            knowledge_action,
            config=config,
            callbacks=callbacks,
            _node_type=NODE_TYPE_KNOWLEDGE,
        )

    def add_mcp_node(
        self,
        name: str,
        mcp_toolset: MCPToolset,
        selected_tool_name: str,
        req_src_node: str,
        *,
        config: Optional[NodeConfig] = None,
        callbacks: Optional[NodeCallbacks] = None,
    ) -> "StateGraph":
        """Add a standalone MCP invocation node.

        This node executes one selected tool from ``mcp_toolset`` with args from:
        ``state[STATE_KEY_NODE_RESPONSES][req_src_node]``.

        Usage note:
            You must add a previous node that builds MCP request args and writes them
            into ``state[STATE_KEY_NODE_RESPONSES][req_src_node]``.
            MCP execution intentionally fails fast (errors are raised) so users can
            see incorrect request payloads immediately.

        Args:
            name: Unique name for the node.
            mcp_toolset: Preconfigured MCPToolset instance.
            selected_tool_name: Exact MCP tool name to invoke.
            req_src_node: Upstream node id where request args are stored in node_responses.
            config: Optional NodeConfig.
            callbacks: Optional node callbacks.
        """
        if mcp_toolset is None:
            raise TypeError(f"MCP toolset for node '{name}' must not be None.")

        selected_name = selected_tool_name.strip()
        if selected_name == "":
            raise ValueError("selected_tool_name must be a non-empty string.")

        source_node_id = req_src_node.strip()
        if source_node_id == "":
            raise ValueError("req_src_node must be a non-empty string.")

        if config is None:
            config = NodeConfig(name=name)
        elif config.name is None:
            config.name = name

        async def mcp_action(
            state: State,
            writer: EventWriter,
            async_writer: AsyncEventWriter,
            ctx: Optional[InvocationContext] = None,
        ) -> dict[str, Any]:
            action = MCPNodeAction(
                name=name,
                mcp_toolset=mcp_toolset,
                selected_tool_name=selected_name,
                req_src_node=source_node_id,
                writer=writer,
                async_writer=async_writer,
                ctx=ctx,
            )
            return await action.execute(state)

        return self.add_node(
            name,
            mcp_action,
            config=config,
            callbacks=callbacks,
            _node_type=NODE_TYPE_TOOL,
        )

    def add_agent_node(
        self,
        node_id: str,
        agent: BaseAgent,
        *,
        config: Optional[NodeConfig] = None,
        callbacks: Optional[NodeCallbacks] = None,
        isolated_messages: bool = False,
        input_from_last_response: bool = False,
        event_scope: Optional[str] = None,
        input_mapper: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        output_mapper: Optional[Callable[[dict[str, Any], SubgraphResult], Optional[dict[str, Any]]]] = None,
    ) -> "StateGraph":
        """Add an agent node that invokes a sub-agent.

        Args:
            node_id: Graph node ID.
            agent: Agent instance to invoke. Must not be None.
            config: Common NodeConfig for the node (optional)
            callbacks: Lifecycle callbacks for this node (optional)
            isolated_messages: If True, child execution does not inherit parent message history.
            input_from_last_response: If True, map parent STATE_KEY_LAST_RESPONSE to child STATE_KEY_USER_INPUT.
            event_scope: Optional branch scope segment for child agent events.
            input_mapper: Function to transform parent state to child state.
            output_mapper: Function to transform child result to parent state update.
                Return None to skip state updates for this node.

        Returns:
            Self for method chaining

        Example:
            >>> from trpc_agent_sdk.dsl.graph import NodeConfig, StateMapper
            >>> config = NodeConfig(name="Researcher", description="Research agent node")
            >>> graph.add_agent_node(
            ...     "research",
            ...     agent=research_agent,
            ...     config=config,
            ...     isolated_messages=True,
            ...     input_mapper=StateMapper.pick("query", "context"),
            ... )
        """
        if agent is None:
            raise TypeError(f"Agent for node '{node_id}' must not be None.")

        if config is None:
            config = NodeConfig(name=node_id)
        elif config.name is None:
            config.name = node_id
        self._agent_nodes[node_id] = agent

        async def agent_action(
            state: State,
            writer: EventWriter,
            async_writer: AsyncEventWriter,
            ctx: Optional[InvocationContext] = None,
            callback_ctx: Optional[NodeCallbackContext] = None,
            callbacks: Optional[NodeCallbacks] = None,
        ) -> dict:
            action = AgentNodeAction(
                node_id=node_id,
                agent=agent,
                node_config=config,
                writer=writer,
                async_writer=async_writer,
                ctx=ctx,
                callback_ctx=callback_ctx,
                callbacks=callbacks,
                isolated_messages=isolated_messages,
                input_from_last_response=input_from_last_response,
                event_scope=event_scope,
                input_mapper=input_mapper,
                output_mapper=output_mapper,
            )
            return await action.execute(state)

        return self.add_node(
            node_id,
            agent_action,
            config=config,
            callbacks=callbacks,
            _node_type=NODE_TYPE_AGENT,
        )

    @property
    def agent_nodes(self) -> dict[str, BaseAgent]:
        """Get agent nodes registered by add_agent_node.

        Returns:
            Copy of {node_id: agent} mappings.
        """
        return dict(self._agent_nodes)

    def add_edge(self, start: str, end: str) -> "StateGraph":
        """Add a directed edge between nodes.

        Args:
            start: Source node name (or START constant)
            end: Target node name (or END constant)

        Returns:
            Self for method chaining
        """
        self._graph.add_edge(start, end)
        return self

    def add_conditional_edges(
        self,
        source: str,
        path: Callable[..., Hashable | list[Hashable]],
        path_map: Optional[dict[Hashable, str]] = None,
    ) -> "StateGraph":
        """Add conditional edges based on a routing function.

        Args:
            source: Source node name
            path: Function that takes state and returns next node name(s)
            path_map: Optional mapping from path return values to node names

        Returns:
            Self for method chaining
        """
        self._graph.add_conditional_edges(source, path, path_map)
        return self

    def set_entry_point(self, key: str) -> "StateGraph":
        """Set the entry point of the graph.

        Shorthand for add_edge(START, key).

        Args:
            key: Name of the first node to execute

        Returns:
            Self for method chaining
        """
        return self.add_edge(START, key)

    def set_finish_point(self, key: str) -> "StateGraph":
        """Set the finish point of the graph.

        Shorthand for add_edge(key, END).

        Args:
            key: Name of the last node to execute

        Returns:
            Self for method chaining
        """
        return self.add_edge(key, END)

    def compile(
        self,
        *,
        memory_saver_option: Optional[MemorySaverOption] = None,
    ) -> "CompiledStateGraph":
        """Compile the graph for execution.

        Args:
            memory_saver_option: Optional MemorySaver settings.

        Returns:
            CompiledStateGraph ready for execution
        """
        option = memory_saver_option or MemorySaverOption()
        memory_saver = MemorySaver(
            auto_persist=option.auto_persist,
            persist_writes=option.persist_writes,
        )
        compiled = self._graph.compile(checkpointer=memory_saver)
        return CompiledStateGraph(compiled, self)


class CompiledStateGraph:
    """Wrapper around a LangGraph compiled graph.

    Maintains reference to the source StateGraph.
    """

    def __init__(self, compiled_graph, source_graph: StateGraph):
        """Initialize the CompiledStateGraph.

        Args:
            compiled_graph: LangGraph compiled graph
            source_graph: The StateGraph this was compiled from
        """
        self._compiled_graph = compiled_graph
        self._source_graph = source_graph

    @property
    def source(self) -> StateGraph:
        """Get the source StateGraph."""
        return self._source_graph

    async def astream(
        self,
        graph_input: Union[dict[str, Any], Command],
        config: dict[str, Any],
        *,
        stream_mode: list[str] | tuple[str, ...],
    ):
        """Stream execution results from the underlying compiled graph."""
        async for item in self._compiled_graph.astream(
                graph_input,
                config,
                stream_mode=stream_mode,
        ):
            yield item

    def get_node_config(self, name: str) -> Optional[NodeConfig]:
        """Get configuration for a specific node.

        Args:
            name: Node name

        Returns:
            NodeConfig or None
        """
        return self._source_graph._node_configs.get(name)
