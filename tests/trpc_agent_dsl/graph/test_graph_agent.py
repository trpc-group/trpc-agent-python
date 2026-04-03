# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for GraphAgent public run behavior."""

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from google.genai.types import Content
from google.genai.types import Part
from langgraph.types import Command
from langgraph.types import Interrupt
from pydantic import ValidationError
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.dsl.graph._constants import ROLE_USER
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_CHECKPOINTS
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_LONG_RUNNING_PREFIX
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_MESSAGES
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_PENDING_INTERRUPT
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_PENDING_INTERRUPT_AUTHOR
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_PENDING_INTERRUPT_BRANCH
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_PENDING_INTERRUPT_ID
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_ACK
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_EVENT
from trpc_agent_sdk.dsl.graph._graph_agent import GraphAgent
from trpc_agent_sdk.dsl.graph._state_graph import CompiledStateGraph
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionResponse


@dataclass
class _RecordedCall:
    """Captured invocation data for fake compiled graph calls."""

    graph_input: Any
    config: dict[str, Any]
    stream_mode: list[str] | tuple[str, ...]


class _FakeCompiledGraph:
    """Simple compiled graph stub with scripted stream outputs."""

    def __init__(self, items: list[tuple[str, Any]] | None = None, error: Exception | None = None):
        self._items = items or []
        self._error = error
        self.calls: list[_RecordedCall] = []

    async def astream(
        self,
        graph_input: Any,
        config: dict[str, Any],
        *,
        stream_mode: list[str] | tuple[str, ...],
    ):
        self.calls.append(_RecordedCall(graph_input=graph_input, config=config, stream_mode=stream_mode))
        if self._error is not None:
            raise self._error
        for item in self._items:
            yield item


def _new_graph_agent(fake_graph: _FakeCompiledGraph) -> GraphAgent:
    """Create a GraphAgent bound to a fake compiled graph."""
    compiled = CompiledStateGraph(fake_graph, object())
    return GraphAgent(name="graph-agent", graph=compiled)


def _new_session(*events: Event, state: dict[str, Any] | None = None) -> Session:
    """Create a minimal Session for GraphAgent tests."""
    return Session(
        id="session-1",
        app_name="app",
        user_id="user",
        save_key="save-key",
        state=state or {},
        events=list(events),
    )


def _new_invocation_context(
    agent: GraphAgent,
    session: Session,
    *,
    invocation_id: str = "inv-1",
    branch: str | None = "graph-agent",
    actions: EventActions | None = None,
) -> InvocationContext:
    """Create a real InvocationContext for GraphAgent.run_async tests."""
    return InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id=invocation_id,
        branch=branch,
        agent=agent,
        agent_context=new_agent_context(),
        session=session,
        event_actions=actions or EventActions(),
    )


@pytest.mark.asyncio
class TestGraphAgent:
    """Public behavior tests for GraphAgent.run_async."""

    async def test_rejects_sub_agents_constructor_field(self):
        """GraphAgent should reject BaseAgent sub_agents construction field."""
        compiled = CompiledStateGraph(_FakeCompiledGraph(items=[]), object())
        with pytest.raises(ValidationError, match="does not accept `sub_agents`"):
            GraphAgent(
                name="graph-agent",
                graph=compiled,
                sub_agents=[],
            )

    async def test_run_async_streams_updates_custom_events_and_completion(self):
        """GraphAgent should pass through stream events and emit graph execution completion."""
        custom_event = Event(
            invocation_id="inv-1",
            author="worker",
            content=Content(role="model", parts=[Part.from_text(text="chunk")]),
            partial=False,
        )
        ack = asyncio.get_running_loop().create_future()
        graph = _FakeCompiledGraph(items=[
            (
                "updates",
                {
                    "worker": {
                        "value": 1,
                        STATE_KEY_LAST_RESPONSE: "done",
                    }
                },
            ),
            (
                "custom",
                {
                    STREAM_KEY_EVENT: custom_event,
                    STREAM_KEY_ACK: ack,
                },
            ),
        ])
        agent = _new_graph_agent(graph)
        user_event = Event(
            invocation_id="inv-1",
            author=ROLE_USER,
            content=Content(role=ROLE_USER, parts=[Part.from_text(text="hello")]),
        )
        session = _new_session(user_event)
        ctx = _new_invocation_context(
            agent,
            session,
            actions=EventActions(state_delta={"persisted": "delta"}),
        )

        events = [event async for event in agent.run_async(ctx)]

        assert len(events) == 3
        assert events[0].object == "graph.state.update"
        assert events[1].get_text() == "chunk"
        completion = events[2]
        assert completion.object == "graph.execution"
        assert completion.actions is not None
        assert completion.actions.state_delta[STATE_KEY_LAST_RESPONSE] == "done"
        assert completion.actions.state_delta["persisted"] == "delta"
        assert completion.actions.state_delta["value"] == 1
        assert completion.actions.state_delta["phase"] == "complete"
        assert ack.done() is True

        assert len(graph.calls) == 1
        call = graph.calls[0]
        assert call.stream_mode == ["updates", "custom"]
        assert isinstance(call.graph_input, dict)
        assert call.graph_input[STATE_KEY_USER_INPUT] == "hello"
        assert call.graph_input[STATE_KEY_MESSAGES] == []

    async def test_run_async_uses_checkpoint_state_without_replaying_history(self):
        """When checkpoint exists, GraphAgent should not rebuild full message history."""
        graph = _FakeCompiledGraph(items=[])
        agent = _new_graph_agent(graph)
        user_event = Event(
            invocation_id="inv-1",
            author=ROLE_USER,
            content=Content(role=ROLE_USER, parts=[Part.from_text(text="latest")]),
        )
        session = _new_session(
            user_event,
            state={
                STATE_KEY_CHECKPOINTS: {
                    "thread": {}
                },
                STATE_KEY_MESSAGES: [Content(role="model", parts=[Part.from_text(text="stale")])],
            },
        )
        ctx = _new_invocation_context(agent, session)

        events = [event async for event in agent.run_async(ctx)]

        assert len(events) == 1
        assert events[0].object == "graph.execution"
        assert events[0].actions is not None
        assert events[0].actions.state_delta["phase"] == "complete"
        call = graph.calls[0]
        assert isinstance(call.graph_input, dict)
        assert STATE_KEY_MESSAGES not in call.graph_input
        assert call.graph_input[STATE_KEY_USER_INPUT] == "latest"

    async def test_run_async_interrupt_emits_bridge_events_and_pauses_completion(self):
        """Interrupt output should produce bridge events and skip graph completion."""
        interrupt = Interrupt(value={"prompt": "approve"}, id="approval_finance_interrupt")
        graph = _FakeCompiledGraph(items=[("updates", {"__interrupt__": interrupt})])
        agent = _new_graph_agent(graph)
        session = _new_session()
        ctx = _new_invocation_context(
            agent,
            session,
            branch="root.graph",
            actions=EventActions(state_delta={"trace": "x"}),
        )

        events = [event async for event in agent.run_async(ctx)]

        assert len(events) == 3
        assert events[0].get_function_calls()[0].name == "graph_interrupt"
        assert events[1].get_function_responses()[0].response == {"prompt": "approve"}
        assert isinstance(events[2], LongRunningEvent)
        assert all(event.object != "graph.execution" for event in events)

        function_call_event = events[0]
        assert function_call_event.actions is not None
        assert function_call_event.actions.state_delta["trace"] == "x"
        assert session.state[STATE_KEY_PENDING_INTERRUPT] is True
        assert isinstance(session.state[STATE_KEY_PENDING_INTERRUPT_ID], str)
        assert session.state[STATE_KEY_PENDING_INTERRUPT_ID].startswith(STATE_KEY_LONG_RUNNING_PREFIX)
        assert session.state[STATE_KEY_PENDING_INTERRUPT_AUTHOR] == "graph-agent"
        assert session.state[STATE_KEY_PENDING_INTERRUPT_BRANCH] == "root.graph"

    async def test_run_async_resume_uses_command_and_clears_pending_interrupt_state(self):
        """User function response should resume graph and clear pending markers."""
        function_response = FunctionResponse(
            id=f"{STATE_KEY_LONG_RUNNING_PREFIX}approval:1",
            name="approval",
            response={"approved": True},
        )
        response_event = Event(
            invocation_id="inv-1",
            author=ROLE_USER,
            content=Content(role=ROLE_USER, parts=[Part(function_response=function_response)]),
        )
        graph = _FakeCompiledGraph(items=[])
        agent = _new_graph_agent(graph)
        session = _new_session(
            response_event,
            state={
                STATE_KEY_PENDING_INTERRUPT: True,
                STATE_KEY_PENDING_INTERRUPT_ID: function_response.id,
                STATE_KEY_PENDING_INTERRUPT_AUTHOR: "graph-agent",
                STATE_KEY_PENDING_INTERRUPT_BRANCH: "root.graph",
            },
        )
        ctx = _new_invocation_context(agent, session, branch="root.graph")

        events = [event async for event in agent.run_async(ctx)]

        assert len(events) == 1
        assert events[0].object == "graph.execution"
        assert events[0].actions is not None
        assert events[0].actions.state_delta[STATE_KEY_PENDING_INTERRUPT] is False
        assert events[0].actions.state_delta[STATE_KEY_PENDING_INTERRUPT_ID] is None
        assert events[0].actions.state_delta[STATE_KEY_PENDING_INTERRUPT_AUTHOR] is None
        assert events[0].actions.state_delta[STATE_KEY_PENDING_INTERRUPT_BRANCH] is None
        call = graph.calls[0]
        assert isinstance(call.graph_input, Command)
        assert call.graph_input.resume == {"approval:1": {"approved": True}}
        assert session.state[STATE_KEY_PENDING_INTERRUPT] is False
        assert session.state[STATE_KEY_PENDING_INTERRUPT_ID] is None
        assert session.state[STATE_KEY_PENDING_INTERRUPT_AUTHOR] is None
        assert session.state[STATE_KEY_PENDING_INTERRUPT_BRANCH] is None

    async def test_run_async_reports_stream_errors_in_completion_event(self):
        """Stream errors should surface in graph execution completion metadata."""
        graph = _FakeCompiledGraph(error=RuntimeError("stream failed"))
        agent = _new_graph_agent(graph)
        session = _new_session()
        ctx = _new_invocation_context(agent, session)

        events = [event async for event in agent.run_async(ctx)]

        assert len(events) == 1
        completion = events[0]
        assert completion.object == "graph.execution"
        assert completion.actions is not None
        assert completion.actions.state_delta["phase"] == "error"
        assert completion.actions.state_delta["error"] == "stream failed"
