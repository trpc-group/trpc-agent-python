# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskManager
from a2a.types import (
    Message,
    Part as A2APart,
    Role,
    Task,
    TaskState,
    TaskStatus,
)

from trpc_agent_sdk.events import AgentCancelledEvent
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunLimitException, RunLimitType
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a_v1.converters import create_working_status_event
from trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor import (
    TrpcA2aAgentExecutor,
    TrpcA2aAgentExecutorConfig,
    _metadata_to_dict,
)
from trpc_agent_sdk.server.a2a_v1.executor._task_result_aggregator import (
    TaskResultAggregator,
)
from trpc_agent_sdk.types import Content, Part as GenaiPart


def _make_runner():
    runner = MagicMock(spec=Runner)
    runner.app_name = "test-app"
    runner.cancel_run_async = AsyncMock(return_value=True)
    runner.run_async = AsyncMock(return_value=iter([]))
    runner.session_service = MagicMock()
    runner.session_service.get_session = AsyncMock(return_value=None)
    runner.session_service.create_session = AsyncMock()
    session_mock = MagicMock()
    session_mock.id = "new-session"
    runner.session_service.create_session.return_value = session_mock
    return runner


def _make_context(*, message=None, task_id="task-1", context_id="ctx-1",
                  current_task=None, call_context=None):
    ctx = MagicMock(spec=RequestContext)
    ctx.task_id = task_id
    ctx.context_id = context_id
    ctx.message = message
    ctx.current_task = current_task
    ctx.call_context = call_context
    return ctx


def _make_event_queue():
    queue = MagicMock(spec=EventQueue)
    queue.enqueue_event = AsyncMock()
    return queue


def _execute_patches():
    return (
        patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.convert_a2a_request_to_trpc_agent_run_args",
            new_callable=AsyncMock,
            return_value={
                "user_id": "u1",
                "session_id": "s1",
                "new_message": MagicMock(),
                "run_config": MagicMock(),
            },
        ),
        patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.is_run_cancelled",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.new_agent_context",
            return_value=MagicMock(),
        ),
    )


def _status_states(enqueued) -> list:
    return [
        e.status.state
        for e in enqueued
        if hasattr(e, "status") and e.status is not None
    ]


def _struct_metadata(**fields):
    from google.protobuf.struct_pb2 import Struct

    meta = Struct()
    meta.update(fields)
    return meta


def _user_message(text="hi"):
    return Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text=text)])


def _run_limit_exc():
    return RunLimitException(
        agent_name="agent",
        limit_type=RunLimitType.MAX_LLM_CALLS,
        configured_value=1,
        observed_value=2,
    )


def _failed_events(enqueued):
    return [
        e for e in enqueued
        if hasattr(e, "status") and e.status is not None
        and e.status.state == TaskState.TASK_STATE_FAILED
    ]


def _pre_handle_patches():
    return (
        patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.get_user_session_id",
            new_callable=AsyncMock,
            return_value=("u1", "s1"),
        ),
        patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.is_run_cancelled",
            new_callable=AsyncMock,
            return_value=False,
        ),
    )


def _make_inv_ctx():
    """Invocation context with Struct-safe scalar fields for event conversion."""
    ctx = MagicMock()
    ctx.app_name = "test-app"
    ctx.user_id = "u1"
    ctx.session = MagicMock()
    ctx.session.id = "s1"
    ctx.invocation_id = "inv-1"
    ctx.branch = None
    return ctx


# ---------------------------------------------------------------------------
# _metadata_to_dict
# ---------------------------------------------------------------------------
class TestMetadataToDict:
    def test_none_returns_empty(self):
        assert _metadata_to_dict(None) == {}

    def test_dict_returned_as_is(self):
        payload = {"k": "v"}
        assert _metadata_to_dict(payload) is payload


# ---------------------------------------------------------------------------
# TrpcA2aAgentExecutorConfig
# ---------------------------------------------------------------------------
class TestTrpcA2aAgentExecutorConfig:
    def test_defaults(self):
        config = TrpcA2aAgentExecutorConfig()
        assert config.cancel_wait_timeout == 1.0
        assert config.user_id_extractor is None
        assert config.event_callback is None

    def test_custom_values(self):
        extractor = lambda r: "user"
        config = TrpcA2aAgentExecutorConfig(cancel_wait_timeout=5.0, user_id_extractor=extractor)
        assert config.cancel_wait_timeout == 5.0
        assert config.user_id_extractor is extractor


# ---------------------------------------------------------------------------
# TrpcA2aAgentExecutor.__init__
# ---------------------------------------------------------------------------
class TestTrpcA2aAgentExecutorInit:
    def test_with_runner(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        assert executor._runner is runner

    def test_default_config(self):
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        assert executor._config is not None
        assert executor._config.cancel_wait_timeout == 1.0

    def test_custom_config(self):
        config = TrpcA2aAgentExecutorConfig(cancel_wait_timeout=10.0)
        executor = TrpcA2aAgentExecutor(runner=_make_runner(), config=config)
        assert executor._config.cancel_wait_timeout == 10.0


# ---------------------------------------------------------------------------
# _resolve_runner
# ---------------------------------------------------------------------------
class TestResolveRunner:
    async def test_runner_instance(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        resolved = await executor._resolve_runner()
        assert resolved is runner

    async def test_sync_callable(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=lambda: runner)
        resolved = await executor._resolve_runner()
        assert resolved is runner
        assert executor._runner is runner

    async def test_async_callable(self):
        runner = _make_runner()

        async def make_runner():
            return runner

        executor = TrpcA2aAgentExecutor(runner=make_runner)
        resolved = await executor._resolve_runner()
        assert resolved is runner

    async def test_invalid_type_raises(self):
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        executor._runner = "not a runner"
        with pytest.raises(TypeError, match="Runner must be"):
            await executor._resolve_runner()


# ---------------------------------------------------------------------------
# _user_id_extractor property
# ---------------------------------------------------------------------------
class TestUserIdExtractor:
    def test_returns_extractor_from_config(self):
        extractor = lambda r: "user"
        config = TrpcA2aAgentExecutorConfig(user_id_extractor=extractor)
        executor = TrpcA2aAgentExecutor(runner=_make_runner(), config=config)
        assert executor._user_id_extractor is extractor

    def test_returns_none_when_no_config(self):
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        executor._config = None
        assert executor._user_id_extractor is None


# ---------------------------------------------------------------------------
# _get_user_session_from_task_metadata
# ---------------------------------------------------------------------------
class TestGetUserSessionFromTaskMetadata:
    def test_with_metadata(self):
        # 1.x Task.metadata is a protobuf Struct; this is the production path
        # through _metadata_to_dict -> MessageToDict.
        ctx = _make_context()
        ctx.current_task = Task(
            id="task-1",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            metadata=_struct_metadata(app_name="app", user_id="u1", session_id="s1"),
        )
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        app, user, session = executor._get_user_session_from_task_metadata(ctx)
        assert app == "app"
        assert user == "u1"
        assert session == "s1"

    def test_with_dict_metadata(self):
        ctx = _make_context()
        ctx.current_task = MagicMock()
        ctx.current_task.metadata = {
            "app_name": "app",
            "user_id": "u1",
            "session_id": "s1",
        }
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        app, user, session = executor._get_user_session_from_task_metadata(ctx)
        assert app == "app"
        assert user == "u1"
        assert session == "s1"

    def test_without_task(self):
        ctx = _make_context(current_task=None)
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        app, user, session = executor._get_user_session_from_task_metadata(ctx)
        assert app is None
        assert user is None
        assert session is None

    def test_without_metadata(self):
        ctx = _make_context()
        ctx.current_task = MagicMock()
        ctx.current_task.metadata = None
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        app, user, session = executor._get_user_session_from_task_metadata(ctx)
        assert app is None


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------
class TestCancel:
    async def test_cancel_with_task_metadata(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context()
        ctx.current_task = Task(
            id="task-1",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            metadata=_struct_metadata(app_name="app", user_id="u1", session_id="s1"),
        )
        queue = _make_event_queue()
        await executor.cancel(ctx, queue)
        runner.cancel_run_async.assert_awaited_once()
        assert runner.cancel_run_async.await_args.args[:2] == ("u1", "s1")

    async def test_working_status_metadata_reaches_cancel_via_task_manager(self):
        """working_meta is on the status event; TaskManager merges it onto the Task.

        Cancel must use those ids, not get_user_session_id fallback (which would
        be A2A_USER_ctx-1 / ctx-1 when call_context is unset).
        """
        store = InMemoryTaskStore()
        manager = TaskManager(
            task_store=store,
            context=ServerCallContext(),
            task_id="task-1",
            context_id="ctx-1",
            initial_message=None,
        )
        await manager.save_task_event(
            Task(
                id="task-1",
                context_id="ctx-1",
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
        await manager.save_task_event(
            create_working_status_event(
                task_id="task-1",
                context_id="ctx-1",
                metadata={"app_name": "test-app", "user_id": "u1", "session_id": "s1"},
            )
        )
        stored = await manager.get_task()
        assert stored is not None

        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(current_task=stored)
        ctx.call_context = None
        queue = _make_event_queue()
        await executor.cancel(ctx, queue)

        runner.cancel_run_async.assert_awaited_once()
        assert runner.cancel_run_async.await_args.args[:2] == ("u1", "s1")

    async def test_cancel_fallback_to_context(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(current_task=None)
        ctx.call_context = MagicMock()
        ctx.call_context.user = MagicMock()
        ctx.call_context.user.user_name = "fallback_user"
        queue = _make_event_queue()
        await executor.cancel(ctx, queue)
        runner.cancel_run_async.assert_awaited_once()

    async def test_cancel_no_active_run(self):
        runner = _make_runner()
        runner.cancel_run_async = AsyncMock(return_value=False)
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(current_task=None)
        ctx.call_context = None
        queue = _make_event_queue()
        await executor.cancel(ctx, queue)
        queue.enqueue_event.assert_awaited()


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------
class TestExecute:
    async def test_raises_on_no_message(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=None)
        queue = _make_event_queue()
        with pytest.raises(ValueError, match="A2A request must have a message"):
            await executor.execute(ctx, queue)

    async def test_submitted_event_when_no_current_task(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        runner = _make_runner()

        async def empty_run(**kwargs):
            return
            yield

        runner.run_async = empty_run
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=msg, current_task=None)
        ctx.call_context = None
        queue = _make_event_queue()

        with patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.convert_a2a_request_to_trpc_agent_run_args",
            new_callable=AsyncMock,
            return_value={
                "user_id": "u1",
                "session_id": "s1",
                "new_message": MagicMock(),
                "run_config": MagicMock(),
            },
        ), patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.is_run_cancelled",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.new_agent_context",
            return_value=MagicMock(),
        ):
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)
            calls = queue.enqueue_event.call_args_list
            assert len(calls) >= 2
            # In 1.x the first event must be a Task (submission signal).
            first_event = calls[0].args[0]
            assert isinstance(first_event, Task)
            assert first_event.id == "task-1"

    async def test_cancelled_session_enqueues_cancellation(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=msg, current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()

        with patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.get_user_session_id",
            new_callable=AsyncMock,
            return_value=("u1", "s1"),
        ), patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.is_run_cancelled",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await executor.execute(ctx, queue)
            enqueued_args = [call.args[0] for call in queue.enqueue_event.call_args_list]
            # Should have a cancellation event
            assert any(hasattr(e, "status") and e.status.state == TaskState.TASK_STATE_CANCELED
                       for e in enqueued_args if hasattr(e, "status"))

    async def test_execution_error_enqueues_status_event(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        runner = _make_runner()

        async def failing_run(**kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        runner.run_async = failing_run
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=msg, current_task=None)
        ctx.call_context = None
        queue = _make_event_queue()

        with patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.convert_a2a_request_to_trpc_agent_run_args",
            new_callable=AsyncMock,
            return_value={
                "user_id": "u1",
                "session_id": "s1",
                "new_message": MagicMock(),
                "run_config": MagicMock(),
            },
        ), patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.is_run_cancelled",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "trpc_agent_sdk.server.a2a_v1.executor._a2a_agent_executor.new_agent_context",
            return_value=MagicMock(),
        ):
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)
            enqueued_args = [call.args[0] for call in queue.enqueue_event.call_args_list]
            # a2a-sdk 1.x task-mode forbids a bare Message after the initial Task;
            # the failure must be delivered as a TaskStatusUpdateEvent.
            assert any(
                hasattr(e, "status")
                and e.status.state == TaskState.TASK_STATE_FAILED
                and e.status.HasField("message")
                for e in enqueued_args
            )

    async def test_empty_run_ends_with_completed(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        runner = _make_runner()

        async def empty_run(**kwargs):
            return
            yield

        runner.run_async = empty_run
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=msg, current_task=None)
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

        enqueued = [call.args[0] for call in queue.enqueue_event.call_args_list]
        states = _status_states(enqueued)
        assert states[-1] == TaskState.TASK_STATE_COMPLETED
        assert TaskState.TASK_STATE_WORKING in states
        assert states[-1] != TaskState.TASK_STATE_WORKING

    async def test_execution_error_does_not_append_completed(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        runner = _make_runner()

        async def content_then_fail(**kwargs):
            yield Event(
                invocation_id="inv-1",
                author="agent",
                content=Content(role="model", parts=[GenaiPart(text="hello")]),
                partial=True,
            )
            raise RuntimeError("boom")

        runner.run_async = content_then_fail
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=msg, current_task=None)
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

        enqueued = [call.args[0] for call in queue.enqueue_event.call_args_list]
        states = _status_states(enqueued)
        assert TaskState.TASK_STATE_FAILED in states
        failed_idx = max(i for i, s in enumerate(states) if s == TaskState.TASK_STATE_FAILED)
        assert TaskState.TASK_STATE_COMPLETED not in states[failed_idx:]

    async def test_error_code_event_emits_single_failed_status(self):
        """An Event with error_code already publishes FAILED; do not emit a second terminal."""
        runner = _make_runner()

        async def error_event_run(**kwargs):
            yield Event(
                invocation_id="inv-1",
                author="agent",
                error_code="500",
                error_message="model error",
            )

        runner.run_async = error_event_run
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock(return_value=_make_inv_ctx())
            await executor.execute(ctx, queue)

        enqueued = [call.args[0] for call in queue.enqueue_event.call_args_list]
        states = _status_states(enqueued)
        assert states.count(TaskState.TASK_STATE_FAILED) == 1
        assert TaskState.TASK_STATE_COMPLETED not in states
        failed = _failed_events(enqueued)[-1]
        assert failed.status.HasField("message")
        meta = _metadata_to_dict(getattr(failed, "metadata", None))
        assert meta.get("error_code") == "500"

    async def test_run_limit_error_does_not_append_completed(self):
        msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[A2APart(text="hi")])
        runner = _make_runner()

        async def hit_limit(**kwargs):
            yield Event(
                invocation_id="inv-1",
                author="agent",
                content=Content(role="model", parts=[GenaiPart(text="hello")]),
                partial=True,
            )
            raise RunLimitException(
                agent_name="agent",
                limit_type=RunLimitType.MAX_LLM_CALLS,
                configured_value=1,
                observed_value=2,
            )

        runner.run_async = hit_limit
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=msg, current_task=None)
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

        enqueued = [call.args[0] for call in queue.enqueue_event.call_args_list]
        states = _status_states(enqueued)
        assert TaskState.TASK_STATE_FAILED in states
        failed_idx = max(i for i, s in enumerate(states) if s == TaskState.TASK_STATE_FAILED)
        assert TaskState.TASK_STATE_COMPLETED not in states[failed_idx:]

    async def test_run_limit_during_run_enqueues_failure_with_metadata(self):
        runner = _make_runner()

        async def hit_limit(**kwargs):
            raise _run_limit_exc()
            yield  # pragma: no cover

        runner.run_async = hit_limit
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

        enqueued = [call.args[0] for call in queue.enqueue_event.call_args_list]
        states = _status_states(enqueued)
        assert TaskState.TASK_STATE_FAILED in states
        assert TaskState.TASK_STATE_COMPLETED not in states
        failed = _failed_events(enqueued)[-1]
        meta = _metadata_to_dict(getattr(failed, "metadata", None))
        assert meta.get("error_code") == "max_llm_calls_exceeded"
        assert meta.get("limit_type") == RunLimitType.MAX_LLM_CALLS.value
        assert meta.get("agent_name") == "agent"

    async def test_agent_cancelled_event_enqueues_cancellation(self):
        runner = _make_runner()

        async def cancelled_run(**kwargs):
            yield AgentCancelledEvent(invocation_id="inv-1", author="agent")

        runner.run_async = cancelled_run
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

        enqueued = [call.args[0] for call in queue.enqueue_event.call_args_list]
        states = _status_states(enqueued)
        assert TaskState.TASK_STATE_CANCELED in states
        assert TaskState.TASK_STATE_COMPLETED not in states
        assert TaskState.TASK_STATE_FAILED not in states

    async def test_event_callback_error_enqueues_failure(self):
        runner = _make_runner()

        async def one_event(**kwargs):
            yield Event(invocation_id="inv-1", author="agent")

        def boom_callback(event, context):
            raise RuntimeError("callback boom")

        runner.run_async = one_event
        executor = TrpcA2aAgentExecutor(
            runner=runner,
            config=TrpcA2aAgentExecutorConfig(event_callback=boom_callback),
        )
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

        states = _status_states([call.args[0] for call in queue.enqueue_event.call_args_list])
        assert TaskState.TASK_STATE_FAILED in states
        assert TaskState.TASK_STATE_COMPLETED not in states

    async def test_run_config_factory_error_enqueues_failure(self):
        runner = _make_runner()

        async def empty_run(**kwargs):
            return
            yield  # pragma: no cover

        def bad_factory(_ctx):
            raise RuntimeError("config boom")

        runner.run_async = empty_run
        executor = TrpcA2aAgentExecutor(
            runner=runner,
            config=TrpcA2aAgentExecutorConfig(run_config_factory=bad_factory),
        )
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

        failed = _failed_events([call.args[0] for call in queue.enqueue_event.call_args_list])
        assert failed

    async def test_pre_handle_generic_error_enqueues_failure(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()
        p_session, p_cancelled = _pre_handle_patches()

        with p_session, p_cancelled, patch.object(
                executor, "_handle_request", new_callable=AsyncMock,
                side_effect=RuntimeError("pre-handle boom")):
            await executor.execute(ctx, queue)

        failed = _failed_events([call.args[0] for call in queue.enqueue_event.call_args_list])
        assert failed
        assert failed[-1].status.HasField("message")

    async def test_pre_handle_run_limit_enqueues_failure_with_metadata(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()
        p_session, p_cancelled = _pre_handle_patches()

        with p_session, p_cancelled, patch.object(
                executor, "_handle_request", new_callable=AsyncMock,
                side_effect=_run_limit_exc()):
            await executor.execute(ctx, queue)

        failed = _failed_events([call.args[0] for call in queue.enqueue_event.call_args_list])
        assert failed
        meta = _metadata_to_dict(getattr(failed[-1], "metadata", None))
        assert meta.get("error_code") == "max_llm_calls_exceeded"
        assert meta.get("agent_name") == "agent"

    async def test_pre_handle_error_swallows_failure_enqueue_error(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()
        queue.enqueue_event = AsyncMock(side_effect=RuntimeError("queue down"))
        p_session, p_cancelled = _pre_handle_patches()

        with p_session, p_cancelled, patch.object(
                executor, "_handle_request", new_callable=AsyncMock,
                side_effect=RuntimeError("pre-handle boom")):
            await executor.execute(ctx, queue)

    async def test_handle_request_error_swallows_failure_enqueue_error(self):
        runner = _make_runner()

        async def failing_run(**kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        runner.run_async = failing_run
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()
        # working status succeeds; the subsequent failure event enqueue fails
        queue.enqueue_event = AsyncMock(side_effect=[None, RuntimeError("queue down")])

        p_convert, p_cancelled, p_ctx = _execute_patches()
        with p_convert, p_cancelled, p_ctx:
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)

    async def test_otel_extract_error_is_swallowed(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = MagicMock()
        ctx.call_context.state = {"headers": {"traceparent": "00-abc"}}
        queue = _make_event_queue()
        p_session, p_cancelled = _pre_handle_patches()
        extract = MagicMock(side_effect=RuntimeError("otel boom"))
        handle_request = AsyncMock()

        with patch("opentelemetry.propagate.extract", extract), \
                p_session, p_cancelled, \
                patch.object(executor, "_handle_request", handle_request):
            await executor.execute(ctx, queue)
            extract.assert_called_once()
            handle_request.assert_awaited_once()

    async def test_otel_detach_error_is_swallowed(self):
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=_user_message(), current_task=MagicMock())
        ctx.call_context = MagicMock()
        ctx.call_context.state = {"headers": {"traceparent": "00-abc"}}
        queue = _make_event_queue()
        p_session, p_cancelled = _pre_handle_patches()
        detach = MagicMock(side_effect=RuntimeError("detach boom"))
        handle_request = AsyncMock()

        with patch("opentelemetry.propagate.extract", return_value="otel-ctx"), \
                patch("opentelemetry.context.attach", return_value="token"), \
                patch("opentelemetry.context.detach", detach), \
                p_session, p_cancelled, \
                patch.object(executor, "_handle_request", handle_request):
            await executor.execute(ctx, queue)
            handle_request.assert_awaited_once()
            detach.assert_called_once_with("token")


# ---------------------------------------------------------------------------
# _enqueue_failure_event
# ---------------------------------------------------------------------------
class TestEnqueueFailureEvent:
    async def test_publishes_failed_status(self):
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        ctx = _make_context(message=_user_message())
        queue = _make_event_queue()

        await executor._enqueue_failure_event(queue, ctx, "boom")

        failed = _failed_events([call.args[0] for call in queue.enqueue_event.call_args_list])
        assert len(failed) == 1
        assert failed[0].status.HasField("message")

    async def test_records_failure_on_aggregator(self):
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        ctx = _make_context(message=_user_message())
        queue = _make_event_queue()
        aggregator = TaskResultAggregator()

        await executor._enqueue_failure_event(queue, ctx, "boom", aggregator=aggregator)

        assert aggregator.task_state == TaskState.TASK_STATE_FAILED
        queue.enqueue_event.assert_awaited_once()

    async def test_swallows_enqueue_error(self):
        executor = TrpcA2aAgentExecutor(runner=_make_runner())
        ctx = _make_context(message=_user_message())
        queue = _make_event_queue()
        queue.enqueue_event = AsyncMock(side_effect=RuntimeError("queue down"))
        aggregator = TaskResultAggregator()

        await executor._enqueue_failure_event(
            queue, ctx, "boom", aggregator=aggregator, metadata={"error_code": "x"})

        assert aggregator.task_state == TaskState.TASK_STATE_FAILED


# ---------------------------------------------------------------------------
# _prepare_session
# ---------------------------------------------------------------------------
class TestPrepareSession:
    async def test_creates_session_when_not_found(self):
        runner = _make_runner()
        runner.session_service.get_session = AsyncMock(return_value=None)
        session_mock = MagicMock()
        session_mock.id = "new-session-id"
        runner.session_service.create_session = AsyncMock(return_value=session_mock)

        executor = TrpcA2aAgentExecutor(runner=runner)
        run_args = {"user_id": "u1", "session_id": "s1"}
        session = await executor._prepare_session(run_args, runner)
        assert session is session_mock
        assert run_args["session_id"] == "new-session-id"

    async def test_returns_existing_session(self):
        runner = _make_runner()
        existing_session = MagicMock()
        existing_session.id = "existing"
        runner.session_service.get_session = AsyncMock(return_value=existing_session)

        executor = TrpcA2aAgentExecutor(runner=runner)
        run_args = {"user_id": "u1", "session_id": "s1"}
        session = await executor._prepare_session(run_args, runner)
        assert session is existing_session
        assert run_args["session_id"] == "s1"
