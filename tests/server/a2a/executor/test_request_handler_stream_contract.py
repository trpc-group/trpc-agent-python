# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Contract tests: non-working intermediate states must not truncate the A2A stream.

``TaskResultAggregator`` no longer rewrites intermediate ``TaskStatusUpdateEvent``
states to ``working`` (a2a-sdk 1.x protobuf events are shared and must not be
mutated).  That is safe only if ``DefaultRequestHandler`` keeps consuming after
``input_required`` / ``auth_required``.  These tests drive a real handler and
its ``EventQueue`` to lock that SDK contract in.
"""

from __future__ import annotations

import asyncio

import pytest
from a2a.server.agent_execution import AgentExecutor
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from trpc_agent_sdk.server.a2a.executor._task_result_aggregator import TaskResultAggregator


def _agent_card() -> AgentCard:
    return AgentCard(
        name="stream-contract",
        description="Test agent",
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=""),
        ],
        skills=[],
    )


def _status_event(task_id: str, context_id: str, state: TaskState, text: str) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=TaskStatus(
            state=state,
            message=Message(
                message_id="status-msg",
                role=Role.ROLE_AGENT,
                parts=[Part(text=text)],
            ),
        ),
    )


class _ScriptedExecutor(AgentExecutor):
    """Enqueue Task → working → interrupted status → artifact → completed.

    The interrupted status is observed by ``TaskResultAggregator`` before it is
    published, matching production: the aggregator must not rewrite the event.
    """

    def __init__(self, interrupted_state: TaskState):
        self._interrupted_state = interrupted_state
        self.observed_interrupted_state: TaskState | None = None

    async def execute(self, context, event_queue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message] if context.message else [],
            )
        )
        await event_queue.enqueue_event(
            _status_event(task_id, context_id, TaskState.TASK_STATE_WORKING, "working")
        )

        interrupted = _status_event(
            task_id, context_id, self._interrupted_state, "need input or auth"
        )
        aggregator = TaskResultAggregator()
        aggregator.process_event(interrupted)
        self.observed_interrupted_state = interrupted.status.state
        await event_queue.enqueue_event(interrupted)

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=Artifact(
                    artifact_id="art-1",
                    parts=[Part(text="later artifact")],
                ),
                last_chunk=False,
            )
        )
        await event_queue.enqueue_event(
            _status_event(task_id, context_id, TaskState.TASK_STATE_COMPLETED, "done")
        )

    async def cancel(self, context, event_queue) -> None:
        return


async def _collect_stream(interrupted_state: TaskState) -> tuple[_ScriptedExecutor, list]:
    executor = _ScriptedExecutor(interrupted_state)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=_agent_card(),
    )
    params = SendMessageRequest(
        tenant="",
        message=Message(
            message_id="user-1",
            role=Role.ROLE_USER,
            parts=[Part(text="hello")],
        ),
    )
    try:
        events = []
        async for event in handler.on_message_send_stream(params, ServerCallContext()):
            events.append(event)
        return executor, events
    finally:
        await handler.aclose()


def _status_states(events: list) -> list[TaskState]:
    return [e.status.state for e in events if isinstance(e, TaskStatusUpdateEvent)]


@pytest.mark.parametrize(
    "interrupted_state",
    [
        pytest.param(TaskState.TASK_STATE_INPUT_REQUIRED, id="input_required"),
        pytest.param(TaskState.TASK_STATE_AUTH_REQUIRED, id="auth_required"),
    ],
)
@pytest.mark.asyncio
async def test_non_working_intermediate_status_does_not_truncate_stream(interrupted_state):
    executor, events = await asyncio.wait_for(
        _collect_stream(interrupted_state),
        timeout=5,
    )

    # Aggregator observes but does not rewrite the shared protobuf event.
    assert executor.observed_interrupted_state == interrupted_state

    states = _status_states(events)
    assert interrupted_state in states
    assert TaskState.TASK_STATE_COMPLETED in states

    interrupted_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, TaskStatusUpdateEvent) and e.status.state == interrupted_state
    )
    later = events[interrupted_idx + 1:]
    assert any(isinstance(e, TaskArtifactUpdateEvent) for e in later), (
        f"{interrupted_state} truncated the stream; later artifact was dropped. "
        f"events after interrupt: {[type(e).__name__ for e in later]}"
    )
    assert any(
        isinstance(e, TaskStatusUpdateEvent) and e.status.state == TaskState.TASK_STATE_COMPLETED
        for e in later
    ), (
        f"{interrupted_state} truncated the stream; completed status was dropped. "
        f"events after interrupt: {[type(e).__name__ for e in later]}"
    )
    artifact = next(e for e in later if isinstance(e, TaskArtifactUpdateEvent))
    assert artifact.artifact.parts[0].text == "later artifact"
