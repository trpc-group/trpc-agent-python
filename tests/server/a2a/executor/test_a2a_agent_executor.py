# -*- coding: utf-8 -*-
"""Unit tests for trpc_agent_sdk.server.a2a.executor._a2a_agent_executor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Message,
    Part as A2APart,
    Role,
    TaskState,
    TaskStatus,
    TextPart,
)

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a.executor._a2a_agent_executor import (
    TrpcA2aAgentExecutor,
    TrpcA2aAgentExecutorConfig,
)


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
        ctx.current_task = MagicMock()
        ctx.current_task.metadata = {
            "app_name": "app",
            "user_id": "u1",
            "session_id": "s1",
        }
        queue = _make_event_queue()
        await executor.cancel(ctx, queue)
        runner.cancel_run_async.assert_awaited_once()

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
        msg = Message(message_id="m1", role=Role.user, parts=[A2APart(root=TextPart(text="hi"))])
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
            "trpc_agent_sdk.server.a2a.executor._a2a_agent_executor.convert_a2a_request_to_trpc_agent_run_args",
            new_callable=AsyncMock,
            return_value={
                "user_id": "u1",
                "session_id": "s1",
                "new_message": MagicMock(),
                "run_config": MagicMock(),
            },
        ), patch(
            "trpc_agent_sdk.server.a2a.executor._a2a_agent_executor.is_run_cancelled",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "trpc_agent_sdk.server.a2a.executor._a2a_agent_executor.new_agent_context",
            return_value=MagicMock(),
        ):
            runner._new_invocation_context = MagicMock()
            await executor.execute(ctx, queue)
            calls = queue.enqueue_event.call_args_list
            assert len(calls) >= 2

    async def test_cancelled_session_enqueues_cancellation(self):
        msg = Message(message_id="m1", role=Role.user, parts=[A2APart(root=TextPart(text="hi"))])
        runner = _make_runner()
        executor = TrpcA2aAgentExecutor(runner=runner)
        ctx = _make_context(message=msg, current_task=MagicMock())
        ctx.call_context = None
        queue = _make_event_queue()

        with patch(
            "trpc_agent_sdk.server.a2a.executor._a2a_agent_executor.get_user_session_id",
            new_callable=AsyncMock,
            return_value=("u1", "s1"),
        ), patch(
            "trpc_agent_sdk.server.a2a.executor._a2a_agent_executor.is_run_cancelled",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await executor.execute(ctx, queue)
            enqueued_args = [call.args[0] for call in queue.enqueue_event.call_args_list]
            # Should have a cancellation event
            assert any(hasattr(e, "status") and e.status.state == TaskState.canceled
                       for e in enqueued_args if hasattr(e, "status"))


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
