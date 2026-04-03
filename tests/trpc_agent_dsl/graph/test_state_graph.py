# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public-API tests for StateGraph and CompiledStateGraph."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from langgraph.errors import GraphInterrupt
from trpc_agent_sdk.dsl.graph._callbacks import NodeCallbacks
from trpc_agent_sdk.dsl.graph._constants import END
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_METADATA
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_STEP_NUMBER
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_ACK
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_EVENT
from trpc_agent_sdk.dsl.graph._memory_saver import MemorySaverOption
from trpc_agent_sdk.dsl.graph._node_config import NodeConfig
from trpc_agent_sdk.dsl.graph._state import State
from trpc_agent_sdk.dsl.graph._state_graph import CompiledStateGraph
from trpc_agent_sdk.dsl.graph._state_graph import StateGraph


class _AckingWriter:
    """Captures stream payloads and resolves async acknowledgements."""

    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)
        ack = payload.get(STREAM_KEY_ACK)
        if ack is not None and not ack.done():
            ack.set_result(True)


def _capture_added_wrapper(graph: StateGraph, register_node) -> tuple[Any, dict[str, Any]]:
    """Capture wrapped node callable registered on underlying LangGraph object."""
    captured: dict[str, Any] = {}

    def fake_add_node(name, wrapper, **kwargs):
        captured["name"] = name
        captured["wrapper"] = wrapper
        captured["kwargs"] = kwargs

    with patch.object(graph._graph, "add_node", side_effect=fake_add_node):
        register_node()

    return captured["wrapper"], captured["kwargs"]


class TestStateGraphValidation:
    """Tests for basic StateGraph validation paths."""

    def test_add_node_rejects_non_async_action(self):
        """Graph nodes should enforce async actions to match runtime contract."""
        graph = StateGraph(State)

        def sync_action(state):
            del state
            return {}

        with pytest.raises(TypeError, match="must be async"):
            graph.add_node("sync", sync_action)

    def test_add_agent_node_rejects_none_agent(self):
        """Agent node registration should fail fast for missing agent references."""
        graph = StateGraph(State)

        with pytest.raises(TypeError, match="must not be None"):
            graph.add_agent_node("sub", agent=None)

    def test_add_node_sets_default_config_name(self):
        """NodeConfig name should be normalized through public add_node + compile path."""
        graph = StateGraph(State)
        config = NodeConfig(name=None)

        async def action(state):
            del state
            return {}

        graph.add_node("worker", action, config=config)
        graph.set_entry_point("worker")
        graph.set_finish_point("worker")
        compiled = graph.compile()
        stored = compiled.get_node_config("worker")

        assert stored is not None
        assert stored.name == "worker"
        assert config.name == "worker"


class TestStateGraphBuilders:
    """Tests for node-builder convenience methods and compile wrapper."""

    async def test_add_llm_and_agent_node_builders_create_callable_actions(self):
        """Builder helpers should wrap corresponding NodeAction execute methods."""
        graph = StateGraph(State)
        model = SimpleNamespace(name="demo-model")
        tools = {"tool_a": object()}
        sub_agent = SimpleNamespace(name="sub-agent")

        captured_actions: dict[str, callable] = {}
        captured_configs: dict[str, NodeConfig] = {}
        captured_node_types: dict[str, str] = {}

        def fake_add_node(name, action, *, config=None, callbacks=None, _node_type="function"):
            del callbacks
            captured_actions[name] = action
            captured_configs[name] = config
            captured_node_types[name] = _node_type
            return graph

        with patch.object(StateGraph, "add_node", side_effect=fake_add_node), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.LLMNodeAction") as llm_action_cls, patch(
                    "trpc_agent_sdk.dsl.graph._state_graph.AgentNodeAction") as agent_action_cls:
            llm_instance = llm_action_cls.return_value
            llm_instance.execute = AsyncMock(return_value={"llm": True})
            agent_instance = agent_action_cls.return_value
            agent_instance.execute = AsyncMock(return_value={"agent": True})

            llm_config = NodeConfig(name="llm-node")
            graph.add_llm_node(
                "llm",
                model,
                "instruction",
                tools=tools,
                tool_parallel=True,
                max_tool_iterations=3,
                config=llm_config,
            )
            agent_config = NodeConfig(name="agent-node")
            graph.add_agent_node(
                "agent",
                sub_agent,
                config=agent_config,
                input_mapper=lambda state: {"in": state.get("x")},
                output_mapper=lambda parent, child: {"out": child.last_response},
            )

            assert captured_node_types["llm"] == "llm"
            assert captured_node_types["agent"] == "agent"

            writer = object()
            async_writer = object()
            state = {"x": 1}

            llm_result = await captured_actions["llm"](state, writer, async_writer, ctx=None)
            agent_result = await captured_actions["agent"](
                state,
                writer,
                async_writer,
                ctx=None,
                callback_ctx=None,
                callbacks=None,
            )

        assert llm_result == {"llm": True}
        assert agent_result == {"agent": True}
        assert llm_action_cls.call_args is not None
        assert llm_action_cls.call_args.args[3] == tools
        assert llm_action_cls.call_args.kwargs["tool_parallel"] is True
        assert llm_action_cls.call_args.kwargs["max_tool_iterations"] == 3
        assert agent_action_cls.call_args is not None
        assert callable(agent_action_cls.call_args.kwargs["input_mapper"])
        assert callable(agent_action_cls.call_args.kwargs["output_mapper"])

    def test_add_llm_node_rejects_non_positive_max_tool_iterations(self):
        """max_tool_iterations should reject non-positive values."""
        graph = StateGraph(State)
        model = SimpleNamespace(name="demo-model")

        with pytest.raises(ValueError, match="greater than 0"):
            graph.add_llm_node(
                "llm",
                model,
                "instruction",
                max_tool_iterations=0,
            )

    def test_graph_helpers_are_chainable_and_compile_returns_wrapper(self):
        """Public graph helper methods should be chainable and compile should succeed."""
        graph = StateGraph(State)

        async def action(state):
            del state
            return {}

        graph.add_node("node", action)

        assert graph.set_entry_point("node") is graph
        assert graph.set_finish_point("node") is graph

        branch_graph = StateGraph(State)
        branch_graph.add_node("router", action)
        route = lambda state: END  # noqa: E731
        assert branch_graph.add_conditional_edges("router", route, {END: END}) is branch_graph

        compiled = graph.compile(memory_saver_option=MemorySaverOption(auto_persist=True, persist_writes=True))
        assert isinstance(compiled, CompiledStateGraph)
        assert compiled.source is graph

    async def test_compiled_state_graph_streams_and_exposes_node_configs(self):
        """CompiledStateGraph should proxy stream output and node config lookup."""

        class _FakeCompiled:

            async def astream(self, graph_input, config, *, stream_mode):
                del graph_input, config, stream_mode
                yield ("updates", {"node": {"x": 1}})
                yield ("custom", {"event": "payload"})

        source = StateGraph(State)

        async def action(state):
            del state
            return {}

        source.add_node("node", action)
        compiled = CompiledStateGraph(_FakeCompiled(), source)

        items = []
        async for item in compiled.astream({}, {"configurable": {}}, stream_mode=["updates", "custom"]):
            items.append(item)

        assert items[0][0] == "updates"
        assert items[1][0] == "custom"
        node_config = compiled.get_node_config("node")
        assert node_config is not None
        assert node_config.name == "node"
        assert compiled.get_node_config("missing") is None


class TestStateGraphWrapperExecution:
    """Tests for add_node runtime wrapper behavior."""

    async def test_wrapper_injects_dependencies_and_runs_callbacks(self):
        """Wrapper should inject dependencies and apply callbacks in merged order."""
        callback_hits: list[str] = []

        global_callbacks = NodeCallbacks()
        node_callbacks = NodeCallbacks()

        async def global_before(ctx, state):
            del state
            callback_hits.append(f"before:{ctx.step_number}")
            return None

        async def node_after(ctx, state, result, error):
            del ctx, state, error
            callback_hits.append(f"node_after:{result['payload']}")
            modified = dict(result)
            modified["payload"] = "node-modified"
            return modified

        async def global_after(ctx, state, result, error):
            del ctx, state, error
            callback_hits.append(f"global_after:{result['payload']}")
            return None

        global_callbacks.register_before_node(global_before)
        global_callbacks.register_after_node(global_after)
        node_callbacks.register_after_node(node_after)

        graph = StateGraph(State, callbacks=global_callbacks)
        captured_args: dict[str, Any] = {}

        async def action(state, writer, async_writer, ctx, callback_ctx, callbacks):
            captured_args["writer"] = writer
            captured_args["async_writer"] = async_writer
            captured_args["ctx"] = ctx
            captured_args["callback_ctx"] = callback_ctx
            captured_args["callbacks"] = callbacks
            assert state["input"] == "value"
            return {"payload": "raw"}

        wrapper, _ = _capture_added_wrapper(
            graph,
            lambda: graph.add_node("worker", action, callbacks=node_callbacks),
        )

        sink = _AckingWriter()
        invocation_ctx = SimpleNamespace(name="ctx")
        state = {
            STATE_KEY_METADATA: {
                "invocation_id": "inv-1",
                "branch": "root.worker",
                "session_id": "session-1",
            },
            STATE_KEY_STEP_NUMBER: 3,
            "input": "value",
        }
        with patch("trpc_agent_sdk.dsl.graph._state_graph.get_stream_writer", return_value=sink), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.get_config",
                return_value={"configurable": {
                    "invocation_context": invocation_ctx
                }}):
            result = await wrapper(state)

        assert result["payload"] == "node-modified"
        assert result[STATE_KEY_STEP_NUMBER] == 4
        assert captured_args["ctx"] is invocation_ctx
        assert captured_args["callback_ctx"].step_number == 3
        assert captured_args["writer"].author == "worker"
        assert captured_args["async_writer"].author == "worker"
        assert captured_args["callbacks"] is not None
        assert callback_hits == [
            "before:3",
            "node_after:raw",
            "global_after:node-modified",
        ]
        emitted_events = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert len(emitted_events) == 2

    async def test_before_callback_can_short_circuit_execution(self):
        """Before callback non-None return should bypass node action and event emission."""
        callbacks = NodeCallbacks()
        action_called = False

        async def before_cb(ctx, state):
            del ctx, state
            return {"short_circuit": True}

        callbacks.register_before_node(before_cb)

        graph = StateGraph(State, callbacks=callbacks)

        async def action(state):
            del state
            nonlocal action_called
            action_called = True
            return {"unexpected": True}

        wrapper, _ = _capture_added_wrapper(graph, lambda: graph.add_node("worker", action))
        sink = _AckingWriter()

        with patch("trpc_agent_sdk.dsl.graph._state_graph.get_stream_writer", return_value=sink), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.get_config",
                return_value={"configurable": {}}):
            result = await wrapper({"input": "value"})

        assert result == {"short_circuit": True}
        assert action_called is False
        assert sink.payloads == []

    async def test_wrapper_converts_none_return_to_empty_update(self):
        """None return should be normalized to empty update and still increment step number."""
        graph = StateGraph(State)

        async def action(state):
            del state
            return None

        wrapper, _ = _capture_added_wrapper(graph, lambda: graph.add_node("worker", action))
        sink = _AckingWriter()

        with patch("trpc_agent_sdk.dsl.graph._state_graph.get_stream_writer", return_value=sink), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.get_config",
                return_value={"configurable": {}}):
            result = await wrapper({STATE_KEY_STEP_NUMBER: 1})

        assert result == {STATE_KEY_STEP_NUMBER: 2}
        emitted_events = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert len(emitted_events) == 2

    async def test_wrapper_raises_when_ctx_is_required_but_missing(self):
        """Missing invocation context should raise and trigger node-error callbacks."""
        callback_errors: list[str] = []
        callbacks = NodeCallbacks()

        async def on_error(ctx, state, error):
            del state
            callback_errors.append(f"{ctx.node_id}:{str(error)}")

        callbacks.register_on_error(on_error)
        graph = StateGraph(State)

        async def action(state, ctx):
            del state, ctx
            return {}

        wrapper, _ = _capture_added_wrapper(graph, lambda: graph.add_node("worker", action, callbacks=callbacks))
        sink = _AckingWriter()

        with patch("trpc_agent_sdk.dsl.graph._state_graph.get_stream_writer", return_value=sink), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.get_config",
                return_value={"configurable": {}}):
            with pytest.raises(RuntimeError, match="requires InvocationContext"):
                await wrapper({STATE_KEY_STEP_NUMBER: 2})

        emitted_events = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert len(emitted_events) == 2
        assert callback_errors
        assert callback_errors[0].startswith("worker:")

    async def test_wrapper_rejects_non_dict_returns(self):
        """Node wrapper should fail fast when action returns unsupported type."""
        graph = StateGraph(State)

        async def action(state):
            del state
            return "bad"

        wrapper, _ = _capture_added_wrapper(graph, lambda: graph.add_node("worker", action))
        sink = _AckingWriter()

        with patch("trpc_agent_sdk.dsl.graph._state_graph.get_stream_writer", return_value=sink), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.get_config",
                return_value={"configurable": {}}):
            with pytest.raises(TypeError, match="must return a dict or None"):
                await wrapper({})

        emitted_events = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert len(emitted_events) == 2

    async def test_wrapper_propagates_graph_interrupt_without_node_error(self):
        """GraphInterrupt should propagate as control flow and skip node-error event."""
        graph = StateGraph(State)

        async def action(state):
            del state
            raise GraphInterrupt()

        wrapper, _ = _capture_added_wrapper(graph, lambda: graph.add_node("worker", action))
        sink = _AckingWriter()

        with patch("trpc_agent_sdk.dsl.graph._state_graph.get_stream_writer", return_value=sink), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.get_config",
                return_value={"configurable": {}}):
            with pytest.raises(GraphInterrupt):
                await wrapper({})

        emitted_events = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert len(emitted_events) == 1


class TestStateGraphBuilderCoverage:
    """Additional tests for builder helper coverage and validation."""

    def test_add_node_without_metadata_falls_back_to_plain_registration(self):
        """When metadata adapter returns empty dict, graph node registration omits metadata arg."""
        graph = StateGraph(State)
        captured_kwargs: dict[str, Any] = {}

        async def action(state):
            del state
            return {}

        def fake_add_node(name, wrapper, **kwargs):
            del name, wrapper
            captured_kwargs.update(kwargs)

        config = NodeConfig(name="worker")
        with patch.object(NodeConfig, "to_metadata", return_value={}):
            with patch.object(graph._graph, "add_node", side_effect=fake_add_node):
                graph.add_node("worker", action, config=config)

        assert captured_kwargs == {}

    async def test_add_code_knowledge_and_mcp_builders_delegate_to_actions(self):
        """Helper builders should construct matching NodeAction classes and execute their actions."""
        graph = StateGraph(State)
        captured_actions: dict[str, Any] = {}
        captured_configs: dict[str, NodeConfig | None] = {}
        captured_types: dict[str, str] = {}

        def fake_add_node(name, action, *, config=None, callbacks=None, _node_type="function"):
            del callbacks
            captured_actions[name] = action
            captured_configs[name] = config
            captured_types[name] = _node_type
            return graph

        with patch.object(StateGraph, "add_node", side_effect=fake_add_node), patch(
                "trpc_agent_sdk.dsl.graph._state_graph.CodeNodeAction") as code_action_cls, patch(
                    "trpc_agent_sdk.dsl.graph._state_graph.KnowledgeNodeAction") as knowledge_action_cls, patch(
                        "trpc_agent_sdk.dsl.graph._state_graph.MCPNodeAction") as mcp_action_cls:
            code_action_cls.return_value.execute = AsyncMock(return_value={"code": True})
            knowledge_action_cls.return_value.execute = AsyncMock(return_value={"knowledge": True})
            mcp_action_cls.return_value.execute = AsyncMock(return_value={"mcp": True})

            graph.add_code_node("code", code_executor=SimpleNamespace(), code="print(1)", language="python")
            knowledge_config = NodeConfig(name=None)
            graph.add_knowledge_node("knowledge", query="who", tool=SimpleNamespace(), config=knowledge_config)
            graph.add_mcp_node(
                "mcp",
                mcp_toolset=SimpleNamespace(),
                selected_tool_name=" weather ",
                req_src_node=" req_builder ",
            )

            writer = object()
            async_writer = object()
            state = {"input": 1}
            code_result = await captured_actions["code"](state, writer, async_writer, ctx=None)
            knowledge_result = await captured_actions["knowledge"](state, writer, async_writer, ctx=None)
            mcp_result = await captured_actions["mcp"](state, writer, async_writer, ctx=None)

        assert captured_types["code"] == "code"
        assert captured_types["knowledge"] == "knowledge"
        assert captured_types["mcp"] == "tool"
        assert code_result == {"code": True}
        assert knowledge_result == {"knowledge": True}
        assert mcp_result == {"mcp": True}
        assert knowledge_config.name == "knowledge"
        assert captured_configs["knowledge"] is knowledge_config
        assert code_action_cls.call_args is not None
        assert code_action_cls.call_args.kwargs["code"] == "print(1)"
        assert knowledge_action_cls.call_args is not None
        assert knowledge_action_cls.call_args.kwargs["query"] == "who"
        assert mcp_action_cls.call_args is not None
        assert mcp_action_cls.call_args.kwargs["selected_tool_name"] == "weather"
        assert mcp_action_cls.call_args.kwargs["req_src_node"] == "req_builder"

    def test_add_knowledge_node_validates_required_tool(self):
        """Knowledge node should reject missing tool references."""
        graph = StateGraph(State)

        with pytest.raises(TypeError, match="must not be None"):
            graph.add_knowledge_node("knowledge", query="who", tool=None)

    def test_add_mcp_node_validates_required_fields(self):
        """MCP builder should validate toolset and required identifiers."""
        graph = StateGraph(State)

        with pytest.raises(TypeError, match="must not be None"):
            graph.add_mcp_node("mcp", mcp_toolset=None, selected_tool_name="weather", req_src_node="req")

        with pytest.raises(ValueError, match="selected_tool_name"):
            graph.add_mcp_node("mcp", mcp_toolset=SimpleNamespace(), selected_tool_name=" ", req_src_node="req")

        with pytest.raises(ValueError, match="req_src_node"):
            graph.add_mcp_node("mcp", mcp_toolset=SimpleNamespace(), selected_tool_name="weather", req_src_node=" ")

    def test_add_llm_node_normalizes_missing_config_name(self):
        """LLM builder should set default config names when config is missing or nameless."""
        graph = StateGraph(State)
        model = SimpleNamespace(name="demo-model")
        captured_configs: dict[str, NodeConfig] = {}

        def fake_add_node(name, action, *, config=None, callbacks=None, _node_type="function"):
            del action, callbacks, _node_type
            captured_configs[name] = config
            return graph

        with patch.object(StateGraph, "add_node", side_effect=fake_add_node):
            graph.add_llm_node("llm_default", model, "instruction")
            nameless = NodeConfig(name=None)
            graph.add_llm_node("llm_nameless", model, "instruction", config=nameless)

        assert captured_configs["llm_default"].name == "llm_default"
        assert captured_configs["llm_nameless"].name == "llm_nameless"

    def test_add_knowledge_and_mcp_normalize_missing_config_name(self):
        """Knowledge/MCP builders should assign node names when config is omitted or nameless."""
        graph = StateGraph(State)
        captured_configs: dict[str, NodeConfig | None] = {}

        def fake_add_node(name, action, *, config=None, callbacks=None, _node_type="function"):
            del action, callbacks, _node_type
            captured_configs[name] = config
            return graph

        with patch.object(StateGraph, "add_node", side_effect=fake_add_node):
            graph.add_knowledge_node("knowledge_default", query="query", tool=SimpleNamespace())
            nameless_config = NodeConfig(name=None)
            graph.add_mcp_node(
                "mcp_nameless",
                mcp_toolset=SimpleNamespace(),
                selected_tool_name="tool",
                req_src_node="req",
                config=nameless_config,
            )

        assert captured_configs["knowledge_default"] is not None
        assert captured_configs["knowledge_default"].name == "knowledge_default"
        assert captured_configs["mcp_nameless"] is nameless_config
        assert nameless_config.name == "mcp_nameless"

    def test_add_agent_node_tracks_agents_and_returns_copy(self):
        """add_agent_node should normalize config names and expose copied agent mapping."""
        graph = StateGraph(State)
        captured_configs: dict[str, NodeConfig] = {}

        def fake_add_node(name, action, *, config=None, callbacks=None, _node_type="function"):
            del action, callbacks, _node_type
            captured_configs[name] = config
            return graph

        agent_default = SimpleNamespace(name="a-default")
        agent_named = SimpleNamespace(name="a-named")
        with patch.object(StateGraph, "add_node", side_effect=fake_add_node):
            graph.add_agent_node("agent_default", agent_default)
            nameless = NodeConfig(name=None)
            graph.add_agent_node("agent_nameless", agent_named, config=nameless)

        assert captured_configs["agent_default"].name == "agent_default"
        assert captured_configs["agent_nameless"].name == "agent_nameless"
        assert graph.agent_nodes["agent_default"] is agent_default
        copied = graph.agent_nodes
        copied["agent_default"] = SimpleNamespace(name="mutated")
        assert graph.agent_nodes["agent_default"] is agent_default
