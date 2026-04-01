# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Execution-path tests for AgentNodeAction."""

from typing import Any

import pytest
from google.genai.types import Content
from google.genai.types import Part
from trpc_agent_sdk.dsl.graph._callbacks import NodeCallbackContext
from trpc_agent_sdk.dsl.graph._callbacks import NodeCallbacks
from trpc_agent_sdk.dsl.graph._define import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._define import STATE_KEY_MESSAGES
from trpc_agent_sdk.dsl.graph._define import STATE_KEY_NODE_RESPONSES
from trpc_agent_sdk.dsl.graph._define import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph._define import STREAM_KEY_ACK
from trpc_agent_sdk.dsl.graph._define import STREAM_KEY_EVENT
from trpc_agent_sdk.dsl.graph._event_writer import AsyncEventWriter
from trpc_agent_sdk.dsl.graph._event_writer import EventWriter
from trpc_agent_sdk.dsl.graph._node_action._agent import AgentNodeAction
from trpc_agent_sdk.dsl.graph._node_config import NodeConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import EventActions


class _AckingWriter:
    """Captures writer payloads and resolves async ack futures."""

    def __init__(self):
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.payloads.append(payload)
        ack = payload.get(STREAM_KEY_ACK)
        if ack is not None and not ack.done():
            ack.set_result(True)


class _ScriptedAgent:
    """Agent stub that yields scripted event batches per invocation."""

    def __init__(self, name: str, event_batches: list[list[Event]]):
        self.name = name
        self._event_batches = event_batches
        self.calls: list[Any] = []
        self.parent_agent = None
        self.root_agent = None

    async def run_async(self, ctx):
        call_index = len(self.calls)
        self.calls.append(ctx)
        events = self._event_batches[call_index] if call_index < len(self._event_batches) else []
        for event in events:
            yield event


class _RootAgent:
    """Root-agent stub exposing find_agent lookup."""

    def __init__(self, name: str, mapping: dict[str, Any]):
        self.name = name
        self._mapping = mapping

    def find_agent(self, name: str):
        return self._mapping.get(name)


class _FakeInvocationContext:
    """Minimal invocation context double used by AgentNodeAction."""

    def __init__(self, agent, session: Session, branch: str = "root"):
        self.invocation_id = "inv-1"
        self.branch = branch
        self.agent = agent
        self.session = session
        self.user_content = None
        self.event_actions = EventActions()
        self.callback_state = None
        self.override_messages = None

    def model_copy(self, update: dict[str, Any], deep: bool = False):
        del deep
        clone = _FakeInvocationContext(self.agent, self.session, self.branch)
        clone.__dict__.update(self.__dict__)
        for key, value in update.items():
            setattr(clone, key, value)
        return clone


def _build_action(
    agent,
    config: NodeConfig,
    *,
    ctx: _FakeInvocationContext | None = None,
    callbacks: NodeCallbacks | None = None,
    isolated_messages: bool = False,
    input_from_last_response: bool = False,
    event_scope: str | None = None,
    input_mapper=None,
    output_mapper=None,
):
    """Create AgentNodeAction with concrete event writers."""
    sink = _AckingWriter()
    writer = EventWriter(
        writer=sink,
        invocation_id="inv-1",
        author="node-1",
        branch="root.node-1",
    )
    async_writer = AsyncEventWriter(
        writer=sink,
        invocation_id="inv-1",
        author="node-1",
        branch="root.node-1",
    )
    action = AgentNodeAction(
        node_id="node-1",
        agent=agent,
        node_config=config,
        writer=writer,
        async_writer=async_writer,
        ctx=ctx,
        callback_ctx=NodeCallbackContext(node_id="node-1"),
        callbacks=callbacks,
        isolated_messages=isolated_messages,
        input_from_last_response=input_from_last_response,
        event_scope=event_scope,
        input_mapper=input_mapper,
        output_mapper=output_mapper,
    )
    return action, sink


def _session_with_events(*events: Event) -> Session:
    """Create session fixture for agent-node tests."""
    return Session(
        id="session-1",
        app_name="app",
        user_id="user",
        save_key="save-key",
        state={},
        events=list(events),
    )


class TestAgentNodeActionExecute:
    """Tests for execute flow and branching behavior."""

    async def test_execute_requires_agent_and_invocation_context(self):
        """Agent/context are required for execution."""
        config = NodeConfig(name="node-1")

        with pytest.raises(RuntimeError, match="is None"):
            action, _ = _build_action(agent=None, config=config, ctx=None)
            await action.execute({})

        scripted_agent = _ScriptedAgent("child", [[]])
        action, _ = _build_action(agent=scripted_agent, config=config, ctx=None)
        with pytest.raises(RuntimeError, match="requires InvocationContext"):
            await action.execute({})

    async def test_execute_returns_default_delta_and_emits_child_event(self):
        """Visible child events should be forwarded and reflected in default result."""
        child_event = Event(
            invocation_id="inv-1",
            author="child",
            content=Content(role="model", parts=[Part.from_text(text="child response")]),
            partial=False,
            actions=EventActions(state_delta={"x": 1}),
        )
        scripted_agent = _ScriptedAgent("child", [[child_event]])
        session = _session_with_events()
        ctx = _FakeInvocationContext(scripted_agent, session)
        config = NodeConfig(name="node-1")
        action, sink = _build_action(scripted_agent, config, ctx=ctx)

        result = await action.execute({STATE_KEY_USER_INPUT: "hello"})

        assert result[STATE_KEY_LAST_RESPONSE] == "child response"
        assert result[STATE_KEY_NODE_RESPONSES] == {"node-1": "child response"}
        assert result[STATE_KEY_USER_INPUT] == ""

        emitted = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert len(emitted) == 1
        assert emitted[0].get_text() == "child response"

    async def test_execute_output_mapper_paths(self):
        """Output mapper should support dict/None and reject invalid return types."""
        child_event = Event(
            invocation_id="inv-1",
            author="child",
            content=Content(role="model", parts=[Part.from_text(text="mapped")]),
            partial=False,
        )
        scripted_agent = _ScriptedAgent("child", [[child_event], [child_event], [child_event]])
        session = _session_with_events()
        ctx = _FakeInvocationContext(scripted_agent, session)

        config_none = NodeConfig(name="node-1")
        action_none, _ = _build_action(
            scripted_agent,
            config_none,
            ctx=ctx,
            output_mapper=lambda parent, child: None,
        )
        assert await action_none.execute({}) == {}

        config_dict = NodeConfig(name="node-1")
        action_dict, _ = _build_action(
            scripted_agent,
            config_dict,
            ctx=ctx,
            output_mapper=lambda parent, child: {"mapped": child.last_response},
        )
        mapped = await action_dict.execute({})
        assert mapped["mapped"] == "mapped"
        assert mapped[STATE_KEY_USER_INPUT] == ""

        config_bad = NodeConfig(name="node-1")
        action_bad, _ = _build_action(
            scripted_agent,
            config_bad,
            ctx=ctx,
            output_mapper=lambda parent, child: "bad",
        )
        with pytest.raises(TypeError, match="must return dict"):
            await action_bad.execute({})

    async def test_execute_handles_transfer_to_target_agent(self):
        """Transfer events should route to target agent without leaking transfer signal."""
        transfer_event = Event(
            invocation_id="inv-1",
            author="current",
            content=Content(role="model", parts=[Part.from_text(text="handoff")]),
            partial=False,
            actions=EventActions(transfer_to_agent="target", state_delta={STATE_KEY_LAST_RESPONSE: "handoff"}),
        )
        final_event = Event(
            invocation_id="inv-1",
            author="target",
            content=Content(role="model", parts=[Part.from_text(text="final")]),
            partial=False,
            actions=EventActions(state_delta={STATE_KEY_NODE_RESPONSES: {
                "target": "final"
            }}),
        )
        current = _ScriptedAgent("current", [[transfer_event]])
        target = _ScriptedAgent("target", [[final_event]])
        root = _RootAgent("root", {"target": target})
        current.root_agent = root
        target.root_agent = root
        target.parent_agent = root

        ctx = _FakeInvocationContext(current, _session_with_events(), branch="root")
        config = NodeConfig(name="node-1")
        action, sink = _build_action(current, config, ctx=ctx)

        result = await action.execute({})

        assert result[STATE_KEY_LAST_RESPONSE] == "final"
        emitted = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert emitted[0].actions.transfer_to_agent is None
        assert target.calls[0].branch == "root.target"

    async def test_execute_handles_missing_transfer_target_with_error_event(self):
        """Unknown transfer target should produce transfer_target_not_found event."""
        transfer_event = Event(
            invocation_id="inv-1",
            author="current",
            content=Content(role="model", parts=[Part.from_text(text="handoff")]),
            partial=False,
            actions=EventActions(transfer_to_agent="missing"),
        )
        current = _ScriptedAgent("current", [[transfer_event]])
        current.root_agent = _RootAgent("root", {})

        ctx = _FakeInvocationContext(current, _session_with_events(), branch="root")
        action, sink = _build_action(current, NodeConfig(name="node-1"), ctx=ctx)

        await action.execute({})

        emitted = [payload[STREAM_KEY_EVENT] for payload in sink.payloads if STREAM_KEY_EVENT in payload]
        assert any(event.error_code == "transfer_target_not_found" for event in emitted)

    async def test_execute_rejects_invisible_transfer_events(self):
        """Invisible transfer requests are invalid and should fail execution."""
        bad_event = Event(
            invocation_id="inv-1",
            author="current",
            content=Content(role="model", parts=[Part.from_text(text="hidden")]),
            partial=False,
            visible=False,
            actions=EventActions(transfer_to_agent="target"),
        )
        current = _ScriptedAgent("current", [[bad_event]])
        ctx = _FakeInvocationContext(current, _session_with_events(), branch="root")
        action, _ = _build_action(current, NodeConfig(name="node-1"), ctx=ctx)

        with pytest.raises(RuntimeError, match="invisible is not allowed"):
            await action.execute({})

    async def test_execute_runs_agent_event_callbacks(self):
        """Agent-event callbacks should run as part of execute() event processing."""
        callback_hits: list[str] = []
        callbacks = NodeCallbacks()

        async def on_agent_event(ctx, state, event):
            del state, event
            callback_hits.append(ctx.node_id)

        callbacks.register_agent_event(on_agent_event)

        child_event = Event(
            invocation_id="inv-1",
            author="child",
            content=Content(role="model", parts=[Part.from_text(text="event")]),
            partial=False,
        )
        scripted_agent = _ScriptedAgent("child", [[child_event]])
        ctx = _FakeInvocationContext(scripted_agent, _session_with_events())
        action, _ = _build_action(
            scripted_agent,
            NodeConfig(name="node-1"),
            ctx=ctx,
            callbacks=callbacks,
        )

        await action.execute({})

        assert callback_hits == ["node-1"]

    async def test_execute_builds_child_history_respecting_isolated_messages(self):
        """execute() should forward parent history unless isolated_messages is enabled."""
        existing = Event(
            invocation_id="inv-1",
            author="user",
            content=Content(role="user", parts=[Part.from_text(text="history")]),
        )
        child_event = Event(
            invocation_id="inv-1",
            author="child",
            content=Content(role="model", parts=[Part.from_text(text="done")]),
            partial=False,
        )

        copied_agent = _ScriptedAgent("child", [[child_event]])
        copied_ctx = _FakeInvocationContext(copied_agent, _session_with_events(existing), branch="root")
        copied_action, _ = _build_action(copied_agent, NodeConfig(name="node-1"), ctx=copied_ctx)

        await copied_action.execute({STATE_KEY_USER_INPUT: "next"})
        copied_texts = [event.get_text() for event in copied_agent.calls[0].session.events if event.content]
        assert copied_texts[:2] == ["history", "next"]

        isolated_agent = _ScriptedAgent("child", [[child_event]])
        isolated_ctx = _FakeInvocationContext(isolated_agent, _session_with_events(existing), branch="root")
        isolated_action, _ = _build_action(
            isolated_agent,
            NodeConfig(name="node-1"),
            ctx=isolated_ctx,
            isolated_messages=True,
        )

        await isolated_action.execute({STATE_KEY_USER_INPUT: "next"})
        isolated_texts = [event.get_text() for event in isolated_agent.calls[0].session.events if event.content]
        assert isolated_texts[:1] == ["next"]

    async def test_execute_ignores_graph_events_for_state_accumulation(self):
        """Graph lifecycle events should not override state-derived response values."""
        graph_event = Event(
            invocation_id="inv-1",
            author="child",
            object="graph.state.update",
            content=Content(role="model", parts=[Part.from_text(text="graph")]),
            partial=False,
            actions=EventActions(state_delta={STATE_KEY_LAST_RESPONSE: "ignore-me"}),
        )
        normal_event = Event(
            invocation_id="inv-1",
            author="child",
            content=Content(role="model", parts=[Part.from_text(text="final")]),
            partial=False,
            actions=EventActions(state_delta={STATE_KEY_LAST_RESPONSE: "final"}),
        )
        scripted_agent = _ScriptedAgent("child", [[graph_event, normal_event]])
        ctx = _FakeInvocationContext(scripted_agent, _session_with_events(), branch="root")
        action, _ = _build_action(scripted_agent, NodeConfig(name="node-1"), ctx=ctx)

        result = await action.execute({})

        assert result[STATE_KEY_LAST_RESPONSE] == "final"
