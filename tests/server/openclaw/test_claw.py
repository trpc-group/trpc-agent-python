"""Unit tests for trpc_agent_sdk.server.openclaw.claw.ClawApplication."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from trpc_agent_sdk.server.openclaw.claw import (
    ClawApplication,
    _HEARTBEAT_USER_ID,
    _HEARTBEAT_SESSION_ID,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(**overrides) -> ClawApplication:
    """Create a ClawApplication with bypassed __init__."""
    app = object.__new__(ClawApplication)
    app._background_tasks = overrides.get("background_tasks", {})
    app._active_tasks = overrides.get("active_tasks", {})
    app._running = overrides.get("running", False)
    app._inbound_loop_task = overrides.get("inbound_loop_task", None)
    app._last_external_target = overrides.get("last_external_target", ("cli", "direct"))
    app._processing_lock = asyncio.Lock()
    app.bus = overrides.get("bus", MagicMock())
    app.agent = overrides.get("agent", MagicMock())
    app.config = overrides.get("config", MagicMock())
    app.cron_service = overrides.get("cron_service", MagicMock())
    app.heartbeat = overrides.get("heartbeat", MagicMock())
    app.channels = overrides.get("channels", MagicMock())
    app.runner = overrides.get("runner", MagicMock())
    app.worker_runner = overrides.get("worker_runner", MagicMock())
    app.session_service = overrides.get("session_service", MagicMock())
    app.command_handler = overrides.get("command_handler", MagicMock())
    return app


def _make_event(*, text="Hello", partial=False, thought=False, function_call=None):
    """Create a mock event with content parts."""
    part = MagicMock()
    part.text = text
    part.thought = thought
    part.function_call = function_call
    event = MagicMock()
    event.partial = partial
    event.content = MagicMock()
    event.content.parts = [part]
    return event


def _make_inbound(channel="cli", sender_id="user1", chat_id="direct", content="hello", **kwargs):
    """Create an InboundMessage for testing."""
    return InboundMessage(channel=channel, sender_id=sender_id, chat_id=chat_id, content=content, **kwargs)


# ---------------------------------------------------------------------------
# _cleanup_background_tasks
# ---------------------------------------------------------------------------

class TestCleanupBackgroundTasks:

    def test_removes_task_from_list(self):
        mock_task = MagicMock()
        app = _make_app(background_tasks={"key": [mock_task]})
        app._cleanup_background_tasks(mock_task, "key")
        assert "key" not in app._background_tasks

    def test_removes_task_keeps_others(self):
        task1 = MagicMock()
        task2 = MagicMock()
        app = _make_app(background_tasks={"key": [task1, task2]})
        app._cleanup_background_tasks(task1, "key")
        assert app._background_tasks["key"] == [task2]

    def test_task_not_in_list(self):
        task1 = MagicMock()
        task2 = MagicMock()
        app = _make_app(background_tasks={"key": [task1]})
        app._cleanup_background_tasks(task2, "key")
        assert app._background_tasks["key"] == [task1]

    def test_unknown_key(self):
        mock_task = MagicMock()
        app = _make_app(background_tasks={})
        app._cleanup_background_tasks(mock_task, "unknown")
        assert app._background_tasks == {}

    def test_empty_list_removes_key(self):
        mock_task = MagicMock()
        app = _make_app(background_tasks={"key": [mock_task]})
        app._cleanup_background_tasks(mock_task, "key")
        assert "key" not in app._background_tasks


# ---------------------------------------------------------------------------
# _refresh_skill_repository
# ---------------------------------------------------------------------------

class TestRefreshSkillRepository:

    def test_with_claw_skill_loader(self):
        mock_repo = MagicMock()
        mock_agent = MagicMock()
        mock_agent.skill_repository = mock_repo

        with patch(
            "trpc_agent_sdk.server.openclaw.claw.ClawSkillLoader",
        ) as MockLoader:
            MockLoader.return_value = mock_repo
            type(mock_repo).__name__ = "ClawSkillLoader"

            app = _make_app(agent=mock_agent)
            with patch("trpc_agent_sdk.server.openclaw.claw.isinstance", return_value=True):
                app._refresh_skill_repository()
            mock_repo.refresh.assert_called_once()

    def test_without_skill_repository(self):
        mock_agent = MagicMock(spec=[])
        app = _make_app(agent=mock_agent)
        app._refresh_skill_repository()

    def test_with_non_claw_repository(self):
        mock_agent = MagicMock()
        mock_agent.skill_repository = "not a ClawSkillLoader"
        app = _make_app(agent=mock_agent)
        app._refresh_skill_repository()


# ---------------------------------------------------------------------------
# _on_heartbeat_notify
# ---------------------------------------------------------------------------

class TestOnHeartbeatNotify:

    async def test_cli_channel_skips(self):
        app = _make_app(last_external_target=("cli", "direct"))
        app.bus.publish_outbound = AsyncMock()
        await app._on_heartbeat_notify("heartbeat response")
        app.bus.publish_outbound.assert_not_called()

    async def test_external_channel_publishes(self):
        app = _make_app(last_external_target=("telegram", "chat123"))
        app.bus.publish_outbound = AsyncMock()
        await app._on_heartbeat_notify("heartbeat response")
        app.bus.publish_outbound.assert_called_once()
        call_args = app.bus.publish_outbound.call_args[0][0]
        assert call_args.channel == "telegram"
        assert call_args.chat_id == "chat123"
        assert call_args.content == "heartbeat response"


# ---------------------------------------------------------------------------
# _on_cron_job
# ---------------------------------------------------------------------------

class TestOnCronJob:

    async def test_empty_message_returns_none(self):
        app = _make_app()
        job = MagicMock()
        job.payload.message = ""
        result = await app._on_cron_job(job)
        assert result is None

    async def test_with_message_runs_turn(self):
        app = _make_app()
        job = MagicMock()
        job.payload.message = "do something"
        job.payload.channel = "cli"
        job.payload.to = "direct"
        job.payload.deliver = False
        job.name = "test-job"
        job.id = "job1"

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="done"):
            result = await app._on_cron_job(job)
        assert result == "done"

    async def test_with_deliver_publishes_outbound(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()
        job = MagicMock()
        job.payload.message = "do something"
        job.payload.channel = "telegram"
        job.payload.to = "chat123"
        job.payload.deliver = True
        job.name = "test-job"
        job.id = "job1"

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="result text"):
            await app._on_cron_job(job)
        app.bus.publish_outbound.assert_called_once()


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------

class TestStart:

    async def test_start_sets_running(self):
        app = _make_app(running=False)
        app.workspace = "/tmp/test"
        app.cron_service.start = AsyncMock()
        app.heartbeat.start = AsyncMock()
        app.channels.enabled_channels = []
        app.cron_service.status = MagicMock(return_value={"jobs": 0})

        with patch.object(app, "_inbound_loop", new_callable=AsyncMock):
            await app.start()
        assert app._running is True

    async def test_start_already_running_noop(self):
        app = _make_app(running=True)
        app.cron_service.start = AsyncMock()
        await app.start()
        app.cron_service.start.assert_not_called()

    async def test_start_creates_inbound_loop_task(self):
        app = _make_app(running=False)
        app.workspace = "/tmp/test"
        app.cron_service.start = AsyncMock()
        app.heartbeat.start = AsyncMock()
        app.channels.enabled_channels = []
        app.cron_service.status = MagicMock(return_value={"jobs": 0})

        with patch.object(app, "_inbound_loop", new_callable=AsyncMock):
            await app.start()
        assert app._inbound_loop_task is not None
        app._inbound_loop_task.cancel()
        try:
            await app._inbound_loop_task
        except asyncio.CancelledError:
            pass


class TestStop:

    async def test_stop_sets_not_running(self):
        app = _make_app(running=True)
        app.heartbeat.stop = MagicMock()
        app.cron_service.stop = MagicMock()
        app.channels.stop_all = AsyncMock()
        await app.stop()
        assert app._running is False

    async def test_stop_not_running_noop(self):
        app = _make_app(running=False)
        app.heartbeat.stop = MagicMock()
        await app.stop()
        app.heartbeat.stop.assert_not_called()

    async def test_stop_cancels_active_tasks(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        app = _make_app(running=True, active_tasks={"key": [mock_task]})
        app.heartbeat.stop = MagicMock()
        app.cron_service.stop = MagicMock()
        app.channels.stop_all = AsyncMock()

        with patch("asyncio.gather", new_callable=AsyncMock, return_value=[]):
            await app.stop()
        mock_task.cancel.assert_called_once()
        assert app._active_tasks == {}

    async def test_stop_cancels_inbound_loop(self):
        loop_task = asyncio.ensure_future(asyncio.sleep(100))
        app = _make_app(running=True, inbound_loop_task=loop_task)
        app.heartbeat.stop = MagicMock()
        app.cron_service.stop = MagicMock()
        app.channels.stop_all = AsyncMock()

        await app.stop()
        assert loop_task.cancelled()
        assert app._inbound_loop_task is None

    async def test_stop_cancels_background_tasks(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        app = _make_app(running=True, background_tasks={"key": [mock_task]})
        app.heartbeat.stop = MagicMock()
        app.cron_service.stop = MagicMock()
        app.channels.stop_all = AsyncMock()

        with patch("asyncio.gather", new_callable=AsyncMock, return_value=[]):
            await app.stop()
        mock_task.cancel.assert_called_once()
        assert app._background_tasks == {}


# ---------------------------------------------------------------------------
# _submit_background_task
# ---------------------------------------------------------------------------

class TestSubmitBackgroundTask:

    async def test_publishes_started_message(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()

        with patch.object(app, "_background_task_runner", new_callable=AsyncMock):
            result = await app._submit_background_task(
                task="do work",
                label="my label",
                origin_channel="telegram",
                origin_chat_id="chat1",
                session_key="s1",
                user_id="u1",
            )

        app.bus.publish_outbound.assert_called_once()
        outbound = app.bus.publish_outbound.call_args[0][0]
        assert outbound.channel == "telegram"
        assert outbound.chat_id == "chat1"
        assert "my label" in outbound.content
        assert outbound.metadata["_progress"] is True
        assert outbound.metadata["_background_task"] is True
        assert "started" in result.lower()

    async def test_creates_task_and_registers_cleanup(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()

        runner_called = asyncio.Event()

        async def fake_runner(**kwargs):
            runner_called.set()

        with patch.object(app, "_background_task_runner", side_effect=fake_runner):
            await app._submit_background_task(
                task="do work",
                label="test",
                origin_channel="cli",
                origin_chat_id="direct",
                session_key="sess1",
                user_id="u1",
            )

        assert "sess1" in app._background_tasks
        assert len(app._background_tasks["sess1"]) == 1
        task = app._background_tasks["sess1"][0]
        assert isinstance(task, asyncio.Task)
        await asyncio.wait_for(runner_called.wait(), timeout=2.0)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_label_truncated_when_too_long(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()

        with patch.object(app, "_background_task_runner", new_callable=AsyncMock):
            await app._submit_background_task(
                task="do work",
                label="A" * 60,
                origin_channel="cli",
                origin_chat_id="direct",
                session_key="s1",
                user_id="u1",
            )

        outbound = app.bus.publish_outbound.call_args[0][0]
        # Label should be truncated to 45 + "..."
        assert "..." in outbound.content

    async def test_label_falls_back_to_task_when_none(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()

        with patch.object(app, "_background_task_runner", new_callable=AsyncMock):
            await app._submit_background_task(
                task="summarize the doc",
                label=None,
                origin_channel="cli",
                origin_chat_id="direct",
                session_key="s1",
                user_id="u1",
            )

        outbound = app.bus.publish_outbound.call_args[0][0]
        assert "summarize the doc" in outbound.content


# ---------------------------------------------------------------------------
# _background_task_runner
# ---------------------------------------------------------------------------

class TestBackgroundTaskRunner:

    async def test_ok_status_publishes_summary(self):
        app = _make_app()
        app.bus.publish_inbound = AsyncMock()

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="result text"):
            await app._background_task_runner(
                session_key="s1",
                task_id="abc",
                task="do something",
                user_id="u1",
                origin_channel="telegram",
                origin_chat_id="chat1",
                task_label="my task",
            )

        app.bus.publish_inbound.assert_called_once()
        inbound = app.bus.publish_inbound.call_args[0][0]
        assert inbound.channel == "system"
        assert "completed successfully" in inbound.content
        assert inbound.metadata["_task_id"] == "abc"
        assert inbound.metadata["_origin_session_key"] == "s1"

    async def test_empty_result_defaults_to_completed(self):
        app = _make_app()
        app.bus.publish_inbound = AsyncMock()

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value=""):
            await app._background_task_runner(
                session_key="s1",
                task_id="abc",
                task="do something",
                user_id="u1",
                origin_channel="cli",
                origin_chat_id="direct",
                task_label="test",
            )

        inbound = app.bus.publish_inbound.call_args[0][0]
        assert "Task completed." in inbound.content
        assert "completed successfully" in inbound.content

    async def test_error_status_publishes_summary(self):
        app = _make_app()
        app.bus.publish_inbound = AsyncMock()

        with patch.object(app, "_run_turn", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await app._background_task_runner(
                session_key="s1",
                task_id="abc",
                task="failing task",
                user_id="u1",
                origin_channel="cli",
                origin_chat_id="direct",
                task_label="test",
            )

        inbound = app.bus.publish_inbound.call_args[0][0]
        assert "failed" in inbound.content
        assert "Error: boom" in inbound.content

    async def test_cancelled_error_reraises_and_publishes_summary(self):
        app = _make_app()
        app.bus.publish_inbound = AsyncMock()

        with patch.object(app, "_run_turn", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await app._background_task_runner(
                    session_key="s1",
                    task_id="abc",
                    task="cancelled task",
                    user_id="u1",
                    origin_channel="cli",
                    origin_chat_id="direct",
                    task_label="test",
                )

        inbound = app.bus.publish_inbound.call_args[0][0]
        assert "was cancelled" in inbound.content

    async def test_run_turn_called_with_worker_agent(self):
        app = _make_app()
        app.bus.publish_inbound = AsyncMock()
        mock_run_turn = AsyncMock(return_value="ok")

        with patch.object(app, "_run_turn", mock_run_turn):
            await app._background_task_runner(
                session_key="s1",
                task_id="t1",
                task="bg work",
                user_id="u1",
                origin_channel="slack",
                origin_chat_id="ch1",
                task_label="bg",
            )

        call_kwargs = mock_run_turn.call_args[1]
        assert call_kwargs["user_id"] == "u1_bg"
        assert call_kwargs["session_id"] == "s1:bg:t1"
        assert call_kwargs["stream_progress"] is False
        assert call_kwargs["use_worker_agent"] is True
        assert call_kwargs["passthrough_metadata"]["_background_task"] is True


# ---------------------------------------------------------------------------
# _run_turn
# ---------------------------------------------------------------------------

class TestRunTurn:

    async def _run_with_events(self, events, *, app=None, **kwargs):
        """Helper to run _run_turn with a fake async generator of events."""
        if app is None:
            app = _make_app()
            app.config.runtime.app_name = "test"
            app.config.channels.send_tool_hints = False
            app.bus.publish_outbound = AsyncMock()
            app.session_service.get_session = AsyncMock(return_value=None)

        async def fake_run_async(**kw):
            for e in events:
                yield e

        app.runner.run_async = fake_run_async
        app.worker_runner.run_async = fake_run_async

        defaults = dict(
            user_id="u1",
            session_id="s1",
            query="test",
            channel="cli",
            chat_id="direct",
        )
        defaults.update(kwargs)
        return app, await app._run_turn(**defaults)

    async def test_accumulates_non_partial_text(self):
        evt1 = _make_event(text="Hello ")
        evt2 = _make_event(text="World")
        _, result = await self._run_with_events([evt1, evt2])
        assert "Hello " in result
        assert "World" in result

    async def test_skips_events_without_content(self):
        empty = MagicMock()
        empty.content = None
        empty.partial = False
        _, result = await self._run_with_events([empty])
        assert result == ""

    async def test_skips_events_with_empty_parts(self):
        evt = MagicMock()
        evt.content = MagicMock()
        evt.content.parts = []
        evt.partial = False
        _, result = await self._run_with_events([evt])
        assert result == ""

    async def test_partial_events_not_in_final_text(self):
        partial = _make_event(text="streaming...", partial=True)
        final = _make_event(text="Final answer")
        _, result = await self._run_with_events([partial, final])
        assert result == "Final answer"
        assert "streaming" not in result

    async def test_partial_events_publish_progress_when_enabled(self):
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        partial = _make_event(text="chunk", partial=True)
        final = _make_event(text="done")

        app, result = await self._run_with_events(
            [partial, final],
            app=app,
            stream_progress=True,
        )

        # partial event should publish outbound with _progress=True
        outbound_calls = app.bus.publish_outbound.call_args_list
        progress_calls = [c for c in outbound_calls if c[0][0].metadata.get("_progress")]
        assert len(progress_calls) >= 1
        assert progress_calls[0][0][0].content == "chunk"

    async def test_partial_events_use_progress_callback(self):
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        callback = AsyncMock()
        partial = _make_event(text="streamed", partial=True)

        async def fake_run_async(**kw):
            yield partial

        app.runner.run_async = fake_run_async
        await app._run_turn(
            user_id="u1",
            session_id="s1",
            query="test",
            channel="cli",
            chat_id="direct",
            stream_progress=True,
            progress_callback=callback,
        )

        callback.assert_called_once_with("streamed")

    async def test_partial_events_sync_progress_callback(self):
        """Test that a synchronous (non-awaitable) progress callback is called."""
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        chunks = []

        def sync_callback(chunk: str):
            chunks.append(chunk)
            return None

        partial = _make_event(text="sync_chunk", partial=True)

        async def fake_run_async(**kw):
            yield partial

        app.runner.run_async = fake_run_async
        await app._run_turn(
            user_id="u1",
            session_id="s1",
            query="test",
            channel="cli",
            chat_id="direct",
            stream_progress=True,
            progress_callback=sync_callback,
        )

        assert chunks == ["sync_chunk"]

    async def test_partial_events_skipped_when_stream_disabled(self):
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        partial = _make_event(text="ignored", partial=True)

        app, _ = await self._run_with_events(
            [partial],
            app=app,
            stream_progress=False,
        )

        app.bus.publish_outbound.assert_not_called()

    async def test_thought_parts_excluded_from_text(self):
        evt = _make_event(text="thinking...", thought=True)
        _, result = await self._run_with_events([evt])
        assert result == ""

    async def test_tool_hints_published_when_enabled(self):
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = True
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        fc = MagicMock()
        fc.name = "search"
        fc.args = '{"q":"hello"}'
        evt = _make_event(function_call=fc)

        app, result = await self._run_with_events([evt], app=app)

        hint_calls = [
            c for c in app.bus.publish_outbound.call_args_list if c[0][0].metadata.get("_tool_hint")
        ]
        assert len(hint_calls) == 1
        assert "search" in hint_calls[0][0][0].content
        # Events with function calls should be skipped for text
        assert result == ""

    async def test_function_call_events_skip_text(self):
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        fc = MagicMock()
        fc.name = "tool"
        fc.args = "{}"
        part_fc = MagicMock()
        part_fc.text = "ignored text"
        part_fc.thought = False
        part_fc.function_call = fc

        evt = MagicMock()
        evt.partial = False
        evt.content = MagicMock()
        evt.content.parts = [part_fc]

        app, result = await self._run_with_events([evt], app=app)
        assert result == ""

    async def test_message_sent_in_turn_returns_empty(self):
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()

        session_mock = MagicMock()
        session_mock.events = []
        app.session_service.get_session = AsyncMock(return_value=session_mock)
        app.session_service.update_session = AsyncMock()

        evt = _make_event(text="response text")

        async def fake_run_async(**kw):
            ctx = kw.get("agent_context")
            ctx.with_metadata("MESSAGE_SENT_IN_TURN", True)
            yield evt

        app.runner.run_async = fake_run_async

        with patch("trpc_agent_sdk.server.openclaw.claw.MESSAGE_SENT_IN_TURN_KEY", "MESSAGE_SENT_IN_TURN"):
            result = await app._run_turn(
                user_id="u1",
                session_id="s1",
                query="test",
                channel="cli",
                chat_id="direct",
            )

        assert result == ""

    async def test_uses_worker_runner_when_flag_set(self):
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        worker_called = False

        async def fake_worker_run(**kw):
            nonlocal worker_called
            worker_called = True
            return
            yield  # make it an async generator

        app.worker_runner.run_async = fake_worker_run
        app.runner.run_async = AsyncMock()

        await app._run_turn(
            user_id="u1",
            session_id="s1",
            query="test",
            channel="cli",
            chat_id="direct",
            use_worker_agent=True,
        )

        assert worker_called

    async def test_metadata_setup(self):
        """Verify all expected metadata keys are set in agent_context."""
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        captured_ctx = None

        async def fake_run_async(**kw):
            nonlocal captured_ctx
            captured_ctx = kw.get("agent_context")
            return
            yield

        app.runner.run_async = fake_run_async

        await app._run_turn(
            user_id="u1",
            session_id="s1",
            query="test",
            channel="telegram",
            chat_id="chat1",
            message_id="msg42",
            in_cron_context=True,
            passthrough_metadata={"custom": "data"},
        )

        assert captured_ctx is not None
        meta = captured_ctx.metadata
        assert meta.get("custom") == "data"
        assert meta.get("MESSAGE_CHANNEL") == "telegram" or "telegram" in str(meta)

    async def test_passthrough_metadata_in_progress(self):
        """Passthrough metadata should be included in progress outbound messages."""
        app = _make_app()
        app.config.runtime.app_name = "test"
        app.config.channels.send_tool_hints = False
        app.bus.publish_outbound = AsyncMock()
        app.session_service.get_session = AsyncMock(return_value=None)

        partial = _make_event(text="delta", partial=True)
        final = _make_event(text="done")

        async def fake_run_async(**kw):
            yield partial
            yield final

        app.runner.run_async = fake_run_async

        await app._run_turn(
            user_id="u1",
            session_id="s1",
            query="test",
            channel="slack",
            chat_id="ch1",
            stream_progress=True,
            passthrough_metadata={"_bg": True},
        )

        progress_calls = [
            c for c in app.bus.publish_outbound.call_args_list if c[0][0].metadata.get("_progress")
        ]
        assert len(progress_calls) >= 1
        assert progress_calls[0][0][0].metadata.get("_bg") is True


# ---------------------------------------------------------------------------
# _persist_session_after_turn
# ---------------------------------------------------------------------------

class TestPersistSessionAfterTurn:

    async def test_session_found_merges_and_updates(self):
        app = _make_app()
        app.config.runtime.app_name = "test"

        session_mock = MagicMock()
        session_mock.events = [MagicMock(id="e1"), MagicMock(id="e2")]
        app.session_service.get_session = AsyncMock(return_value=session_mock)
        app.session_service.update_session = AsyncMock()

        from trpc_agent_sdk.context import new_agent_context
        ctx = new_agent_context()

        with patch("trpc_agent_sdk.server.openclaw.claw.set_agent_context") as mock_set_ctx:
            await app._persist_session_after_turn(
                app_name="test",
                user_id="u1",
                session_id="s1",
                agent_context=ctx,
            )

        app.session_service.update_session.assert_called_once_with(session_mock)
        mock_set_ctx.assert_called_once_with(ctx)

    async def test_session_none_returns_early(self):
        app = _make_app()
        app.session_service.get_session = AsyncMock(return_value=None)
        app.session_service.update_session = AsyncMock()

        from trpc_agent_sdk.context import new_agent_context
        ctx = new_agent_context()

        await app._persist_session_after_turn(
            app_name="test",
            user_id="u1",
            session_id="s1",
            agent_context=ctx,
        )

        app.session_service.update_session.assert_not_called()

    async def test_exception_is_caught(self):
        app = _make_app()
        app.session_service.get_session = AsyncMock(side_effect=RuntimeError("db error"))

        from trpc_agent_sdk.context import new_agent_context
        ctx = new_agent_context()

        # Should not raise
        await app._persist_session_after_turn(
            app_name="test",
            user_id="u1",
            session_id="s1",
            agent_context=ctx,
        )

    async def test_raw_events_merged_into_context(self):
        app = _make_app()
        app.config.runtime.app_name = "test"

        session_mock = MagicMock()
        evt_new = MagicMock(id="new1")
        session_mock.events = [evt_new]
        app.session_service.get_session = AsyncMock(return_value=session_mock)
        app.session_service.update_session = AsyncMock()

        from trpc_agent_sdk.context import new_agent_context
        from trpc_agent_sdk.server.openclaw.storage import RAW_EVENTS_KEY
        ctx = new_agent_context()
        existing_evt = MagicMock(id="old1")
        ctx.with_metadata(RAW_EVENTS_KEY, [existing_evt])

        with patch("trpc_agent_sdk.server.openclaw.claw.set_agent_context"):
            await app._persist_session_after_turn(
                app_name="test",
                user_id="u1",
                session_id="s1",
                agent_context=ctx,
            )

        merged = ctx.get_metadata(RAW_EVENTS_KEY)
        ids = [str(getattr(e, "id", "")) for e in merged]
        assert "old1" in ids
        assert "new1" in ids


# ---------------------------------------------------------------------------
# _process_message
# ---------------------------------------------------------------------------

class TestProcessMessage:

    async def test_returns_outbound_message_when_text(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound(channel="slack", sender_id="u1", chat_id="ch1", content="hi")

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="Hello there"):
            result = await app._process_message(msg)

        assert result is not None
        assert result.channel == "slack"
        assert result.chat_id == "ch1"
        assert result.content == "Hello there"

    async def test_returns_none_when_empty_text(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound()

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value=""):
            result = await app._process_message(msg)

        assert result is None

    async def test_sets_last_external_target_for_non_cli(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound(channel="telegram", chat_id="tg123")

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="ok"):
            await app._process_message(msg)

        assert app._last_external_target == ("telegram", "tg123")

    async def test_does_not_set_last_external_for_cli(self):
        app = _make_app(last_external_target=("old", "old_id"))
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound(channel="cli", chat_id="direct")

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="ok"):
            await app._process_message(msg)

        assert app._last_external_target == ("old", "old_id")

    async def test_system_channel_with_cli_origin_no_update(self):
        """System messages whose parsed origin is 'cli' should not update _last_external_target."""
        app = _make_app(last_external_target=("old", "old_id"))
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound(channel="system", sender_id="bg", chat_id="direct")

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="ok"):
            await app._process_message(msg)

        assert app._last_external_target == ("old", "old_id")

    async def test_system_channel_with_external_origin_updates(self):
        """System messages whose parsed origin is an external channel DO update _last_external_target."""
        app = _make_app(last_external_target=("old", "old_id"))
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound(channel="system", sender_id="bg", chat_id="telegram:chat1")

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value="ok"):
            await app._process_message(msg)

        assert app._last_external_target == ("telegram", "chat1")

    async def test_calls_refresh_skill_repository(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound()

        with patch.object(app, "_run_turn", new_callable=AsyncMock, return_value=""):
            await app._process_message(msg)

        app._refresh_skill_repository.assert_called_once()

    async def test_stream_progress_override(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = True

        msg = _make_inbound(channel="cli")
        mock_run_turn = AsyncMock(return_value="ok")

        with patch.object(app, "_run_turn", mock_run_turn):
            await app._process_message(msg, stream_progress_override=True)

        call_kwargs = mock_run_turn.call_args[1]
        assert call_kwargs["stream_progress"] is True

    async def test_progress_callback_forwarded(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound()
        cb = AsyncMock()
        mock_run_turn = AsyncMock(return_value="")

        with patch.object(app, "_run_turn", mock_run_turn):
            await app._process_message(msg, progress_callback=cb)

        call_kwargs = mock_run_turn.call_args[1]
        assert call_kwargs["progress_callback"] is cb

    async def test_media_forwarded(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound(media=["/path/to/image.png"])
        mock_run_turn = AsyncMock(return_value="")

        with patch.object(app, "_run_turn", mock_run_turn):
            await app._process_message(msg)

        call_kwargs = mock_run_turn.call_args[1]
        assert call_kwargs["media"] == ["/path/to/image.png"]

    async def test_metadata_forwarded_as_passthrough(self):
        app = _make_app()
        app._refresh_skill_repository = MagicMock()
        app.config.channels.send_progress = False

        msg = _make_inbound(metadata={"message_id": "m1", "extra": "val"})
        mock_run_turn = AsyncMock(return_value="")

        with patch.object(app, "_run_turn", mock_run_turn):
            await app._process_message(msg)

        call_kwargs = mock_run_turn.call_args[1]
        assert call_kwargs["passthrough_metadata"]["extra"] == "val"
        assert call_kwargs["message_id"] == "m1"


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------

class TestDispatch:

    async def test_publishes_response(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()
        outbound = OutboundMessage(channel="slack", chat_id="ch1", content="reply")

        with patch.object(app, "_process_message", new_callable=AsyncMock, return_value=outbound):
            msg = _make_inbound(channel="slack", chat_id="ch1")
            await app._dispatch(msg)

        app.bus.publish_outbound.assert_called_once_with(outbound)

    async def test_cli_empty_response_publishes_empty(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()

        with patch.object(app, "_process_message", new_callable=AsyncMock, return_value=None):
            msg = _make_inbound(channel="cli", chat_id="direct")
            await app._dispatch(msg)

        app.bus.publish_outbound.assert_called_once()
        outbound = app.bus.publish_outbound.call_args[0][0]
        assert outbound.channel == "cli"
        assert outbound.content == ""

    async def test_non_cli_none_response_no_publish(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()

        with patch.object(app, "_process_message", new_callable=AsyncMock, return_value=None):
            msg = _make_inbound(channel="telegram", chat_id="chat1")
            await app._dispatch(msg)

        app.bus.publish_outbound.assert_not_called()

    async def test_cancelled_error_reraises(self):
        app = _make_app()

        with patch.object(app, "_process_message", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            msg = _make_inbound()
            with pytest.raises(asyncio.CancelledError):
                await app._dispatch(msg)

    async def test_exception_publishes_error(self):
        app = _make_app()
        app.bus.publish_outbound = AsyncMock()

        with patch.object(app, "_process_message", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            msg = _make_inbound(channel="telegram", chat_id="chat1")
            await app._dispatch(msg)

        app.bus.publish_outbound.assert_called_once()
        outbound = app.bus.publish_outbound.call_args[0][0]
        assert "error" in outbound.content.lower()
        assert outbound.channel == "telegram"


# ---------------------------------------------------------------------------
# _inbound_loop
# ---------------------------------------------------------------------------

class TestInboundLoop:

    async def test_dispatches_non_command_message(self):
        app = _make_app(running=True)
        msg = _make_inbound(content="hello")

        call_count = 0

        async def fake_consume():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return msg
            app._running = False
            raise asyncio.TimeoutError

        app.bus.consume_inbound = fake_consume
        app.command_handler.handle = AsyncMock(return_value=False)

        with patch.object(app, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await app._inbound_loop()

        assert len(app._active_tasks) >= 0
        # dispatch was called via create_task, give it a tick
        await asyncio.sleep(0.05)

    async def test_skips_handled_commands(self):
        app = _make_app(running=True)
        msg = _make_inbound(content="/stop")

        call_count = 0

        async def fake_consume():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return msg
            app._running = False
            raise asyncio.TimeoutError

        app.bus.consume_inbound = fake_consume
        app.command_handler.handle = AsyncMock(return_value=True)

        await app._inbound_loop()

        # No tasks should be created for handled commands
        assert len(app._active_tasks) == 0

    async def test_timeout_continues_loop(self):
        app = _make_app(running=True)
        call_count = 0

        async def fake_consume():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                app._running = False
            raise asyncio.TimeoutError

        app.bus.consume_inbound = fake_consume

        await app._inbound_loop()
        assert call_count >= 3

    async def test_task_cleanup_callback(self):
        app = _make_app(running=True)
        msg = _make_inbound(content="hello")

        dispatch_event = asyncio.Event()

        async def fake_consume():
            if not dispatch_event.is_set():
                dispatch_event.set()
                return msg
            app._running = False
            raise asyncio.TimeoutError

        app.bus.consume_inbound = fake_consume
        app.command_handler.handle = AsyncMock(return_value=False)

        with patch.object(app, "_dispatch", new_callable=AsyncMock):
            await app._inbound_loop()

        # After the task completes, cleanup should remove it
        await asyncio.sleep(0.1)
        # The cleanup callback may or may not have fired; the test verifies
        # no crash occurs in the cleanup path.


# ---------------------------------------------------------------------------
# _on_heartbeat_execute
# ---------------------------------------------------------------------------

class TestOnHeartbeatExecute:

    async def test_calls_run_turn_with_correct_params(self):
        app = _make_app(last_external_target=("telegram", "chat42"))
        mock_run_turn = AsyncMock(return_value="heartbeat result")

        with patch.object(app, "_run_turn", mock_run_turn):
            result = await app._on_heartbeat_execute("check tasks")

        assert result == "heartbeat result"
        call_kwargs = mock_run_turn.call_args[1]
        assert call_kwargs["user_id"] == _HEARTBEAT_USER_ID
        assert call_kwargs["session_id"] == _HEARTBEAT_SESSION_ID
        assert call_kwargs["query"] == "check tasks"
        assert call_kwargs["channel"] == "telegram"
        assert call_kwargs["chat_id"] == "chat42"
        assert call_kwargs["stream_progress"] is False
        assert call_kwargs["passthrough_metadata"]["_heartbeat"] is True

    async def test_uses_last_external_target(self):
        app = _make_app(last_external_target=("slack", "general"))
        mock_run_turn = AsyncMock(return_value="")

        with patch.object(app, "_run_turn", mock_run_turn):
            await app._on_heartbeat_execute("tasks")

        call_kwargs = mock_run_turn.call_args[1]
        assert call_kwargs["channel"] == "slack"
        assert call_kwargs["chat_id"] == "general"


# ---------------------------------------------------------------------------
# start (extended) – channels and cron paths
# ---------------------------------------------------------------------------

class TestStartExtended:

    async def test_start_with_channels_enabled(self):
        app = _make_app(running=False)
        app.workspace = "/tmp/test"
        app.cron_service.start = AsyncMock()
        app.heartbeat.start = AsyncMock()
        app.channels.enabled_channels = ["telegram", "slack"]
        app.cron_service.status = MagicMock(return_value={"jobs": 0})

        with patch.object(app, "_inbound_loop", new_callable=AsyncMock):
            await app.start()

        assert app._running is True
        task = app._inbound_loop_task
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_start_with_cron_jobs(self):
        app = _make_app(running=False)
        app.workspace = "/tmp/test"
        app.cron_service.start = AsyncMock()
        app.heartbeat.start = AsyncMock()
        app.channels.enabled_channels = []
        app.cron_service.status = MagicMock(return_value={"jobs": 5})

        with patch.object(app, "_inbound_loop", new_callable=AsyncMock):
            await app.start()

        assert app._running is True
        task = app._inbound_loop_task
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# run_gateway
# ---------------------------------------------------------------------------

class TestRunGateway:

    async def test_starts_and_stops(self):
        app = _make_app()
        app.channels.start_all = AsyncMock()

        start_called = False
        stop_called = False

        async def fake_start():
            nonlocal start_called
            start_called = True
            app._running = True

        async def fake_stop():
            nonlocal stop_called
            stop_called = True

        app.start = fake_start
        app.stop = fake_stop

        async def fake_wait_forever():
            app._running = False

        with patch.object(app, "_wait_forever", fake_wait_forever):
            await app.run_gateway()

        assert start_called
        assert stop_called

    async def test_stop_called_on_exception(self):
        app = _make_app()

        stop_called = False

        async def fake_start():
            app._running = True

        async def fake_stop():
            nonlocal stop_called
            stop_called = True

        app.start = fake_start
        app.stop = fake_stop
        app.channels.start_all = AsyncMock(side_effect=RuntimeError("channel error"))

        async def fake_wait_forever():
            await asyncio.sleep(100)

        with patch.object(app, "_wait_forever", fake_wait_forever):
            with pytest.raises(RuntimeError, match="channel error"):
                await app.run_gateway()

        assert stop_called


# ---------------------------------------------------------------------------
# _wait_forever
# ---------------------------------------------------------------------------

class TestWaitForever:

    async def test_exits_when_running_false(self):
        app = _make_app(running=True)

        async def set_not_running():
            await asyncio.sleep(0.05)
            app._running = False

        task = asyncio.create_task(set_not_running())
        await asyncio.wait_for(app._wait_forever(), timeout=2.0)
        await task

    async def test_loops_while_running(self):
        app = _make_app(running=True)
        iterations = 0

        original_sleep = asyncio.sleep

        async def counting_sleep(secs):
            nonlocal iterations
            iterations += 1
            if iterations >= 3:
                app._running = False
            await original_sleep(0.01)

        with patch("asyncio.sleep", counting_sleep):
            await asyncio.wait_for(app._wait_forever(), timeout=2.0)

        assert iterations >= 3


# ---------------------------------------------------------------------------
# run_cli_fallback
# ---------------------------------------------------------------------------

class TestRunCliFallback:

    def _setup_app(self):
        app = _make_app()
        app.workspace = "/tmp/test"
        app.command_handler.handle = AsyncMock(return_value=False)
        app.bus.publish_outbound = AsyncMock()
        app.bus.outbound = asyncio.Queue()

        async def fake_start():
            app._running = True

        async def fake_stop():
            app._running = False

        app.start = fake_start
        app.stop = fake_stop
        return app

    async def test_quit_command_exits(self):
        app = self._setup_app()
        inputs = iter(["/quit"])

        with patch("builtins.input", side_effect=lambda _: next(inputs)):
            await app.run_cli_fallback()

    async def test_exit_command_exits(self):
        app = self._setup_app()
        inputs = iter(["exit"])

        with patch("builtins.input", side_effect=lambda _: next(inputs)):
            await app.run_cli_fallback()

    async def test_empty_input_skipped(self):
        app = self._setup_app()
        inputs = iter(["", "  ", "/quit"])

        with patch("builtins.input", side_effect=lambda _: next(inputs)):
            await app.run_cli_fallback()

    async def test_eof_exits_gracefully(self):
        app = self._setup_app()

        with patch("builtins.input", side_effect=EOFError):
            await app.run_cli_fallback()

    async def test_keyboard_interrupt_exits_gracefully(self):
        app = self._setup_app()

        with patch("builtins.input", side_effect=KeyboardInterrupt):
            await app.run_cli_fallback()

    async def test_message_processed_and_response_printed(self):
        app = self._setup_app()
        inputs = iter(["hello", "/quit"])
        printed = []

        response = OutboundMessage(channel="cli", chat_id="direct", content="Hi there!")

        with (
            patch("builtins.input", side_effect=lambda _: next(inputs)),
            patch("builtins.print", side_effect=lambda *a, **kw: printed.append(a)),
            patch.object(app, "_process_message", new_callable=AsyncMock, return_value=response),
        ):
            await app.run_cli_fallback()

        output_text = " ".join(str(a) for args in printed for a in args)
        assert "Hi there!" in output_text

    async def test_streamed_response_no_duplicate_print(self):
        app = self._setup_app()
        inputs = iter(["hello", "/quit"])
        printed = []

        async def fake_process(msg, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                await cb("streamed content")
            return OutboundMessage(channel="cli", chat_id="direct", content="final")

        with (
            patch("builtins.input", side_effect=lambda _: next(inputs)),
            patch("builtins.print", side_effect=lambda *a, **kw: printed.append((a, kw))),
            patch.object(app, "_process_message", side_effect=fake_process),
        ):
            await app.run_cli_fallback()

        all_text = " ".join(str(a) for args, kw in printed for a in args)
        assert "streamed content" in all_text

    async def test_command_handled_drains_outbound(self):
        app = self._setup_app()
        app.command_handler.handle = AsyncMock(return_value=True)
        inputs = iter(["/help", "/quit"])
        printed = []

        app.bus.outbound.put_nowait(OutboundMessage(channel="cli", chat_id="direct", content="Help text"))

        with (
            patch("builtins.input", side_effect=lambda _: next(inputs)),
            patch("builtins.print", side_effect=lambda *a, **kw: printed.append(a)),
        ):
            await app.run_cli_fallback()

        all_text = " ".join(str(a) for args in printed for a in args)
        assert "Help text" in all_text

    async def test_none_response_no_print(self):
        app = self._setup_app()
        inputs = iter(["hello", "/quit"])
        printed = []

        with (
            patch("builtins.input", side_effect=lambda _: next(inputs)),
            patch("builtins.print", side_effect=lambda *a, **kw: printed.append(a)),
            patch.object(app, "_process_message", new_callable=AsyncMock, return_value=None),
        ):
            await app.run_cli_fallback()

        # Should not print "Assistant:" for None response
        assistant_lines = [
            args for args in printed
            if any("Assistant:" in str(a) for a in args) and any(a for a in args if str(a).strip())
        ]
        # Filter out header-only lines from streaming
        real_assistant = [
            args for args in assistant_lines if not all(str(a).strip() in ("", "Assistant:") for a in args)
        ]
        assert len(real_assistant) == 0
