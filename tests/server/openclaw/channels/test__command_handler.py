# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw.channels._command_handler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.channels._command_handler import (
    TrpcClawCommandHandler,
    TrpcClawCommandHandlerParams,
)


def _make_params(**overrides) -> TrpcClawCommandHandlerParams:
    """Build a TrpcClawCommandHandlerParams with sensible mock defaults."""
    config = MagicMock()
    config.runtime.app_name = "test_app"
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    session_service = MagicMock()
    session_service.delete_session = AsyncMock()

    kwargs = dict(
        config=config,
        bus=bus,
        session_service=session_service,
        active_tasks={},
        background_tasks={},
    )
    kwargs.update(overrides)
    return TrpcClawCommandHandlerParams(**kwargs)


def _make_msg(content: str = "/help", **overrides) -> MagicMock:
    """Build a mock InboundMessage."""
    msg = MagicMock()
    msg.content = content
    msg.channel = overrides.get("channel", "cli")
    msg.chat_id = overrides.get("chat_id", "chat1")
    msg.session_key = overrides.get("session_key", "sess1")
    msg.sender_id = overrides.get("sender_id", "user1")
    return msg


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
class TestInit:

    def test_default_callbacks_registered(self):
        handler = TrpcClawCommandHandler(_make_params())
        expected = {"/stop", "/new", "/help", "/restart", "/quit", "/exit", "quit", "exit"}
        assert expected.issubset(handler.callbacks.keys())

    def test_custom_callbacks_merged(self):
        custom_cb = AsyncMock()
        handler = TrpcClawCommandHandler(_make_params(), callback={"/custom": custom_cb})
        assert "/custom" in handler.callbacks
        assert handler.callbacks["/custom"] is custom_cb

    def test_custom_callbacks_can_override_defaults(self):
        custom_help = AsyncMock()
        handler = TrpcClawCommandHandler(_make_params(), callback={"/help": custom_help})
        assert handler.callbacks["/help"] is custom_help

    def test_restarting_initially_false(self):
        handler = TrpcClawCommandHandler(_make_params())
        assert handler.restarting is False


# ---------------------------------------------------------------------------
# handle
# ---------------------------------------------------------------------------
class TestHandle:

    async def test_recognized_command_returns_true(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/help")
        assert await handler.handle(msg) is True

    async def test_unrecognized_command_returns_false(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("hello world")
        assert await handler.handle(msg) is False

    async def test_command_stripped_and_lowered(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("  /HELP  ")
        assert await handler.handle(msg) is True

    async def test_quit_aliases(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        for cmd in ["/quit", "/exit", "quit", "exit"]:
            msg = _make_msg(cmd)
            assert await handler.handle(msg) is True


# ---------------------------------------------------------------------------
# _handle_stop
# ---------------------------------------------------------------------------
class TestHandleStop:

    async def test_no_active_tasks(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/stop")

        await handler.handle(msg)
        call_args = params.bus.publish_outbound.call_args
        assert "No active task" in call_args[0][0].content

    async def test_cancels_active_tasks(self):
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        task.cancel.return_value = True

        params = _make_params(active_tasks={"sess1": [task]})
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/stop")

        with patch("trpc_agent_sdk.server.openclaw.channels._command_handler.asyncio.gather", new_callable=AsyncMock):
            await handler.handle(msg)

        task.cancel.assert_called_once()
        call_args = params.bus.publish_outbound.call_args
        assert "Stopped 1 task(s)" in call_args[0][0].content

    async def test_cancels_background_tasks(self):
        bg_task = MagicMock(spec=asyncio.Task)
        bg_task.done.return_value = False
        bg_task.cancel.return_value = True

        params = _make_params(background_tasks={"sess1": [bg_task]})
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/stop")

        with patch("trpc_agent_sdk.server.openclaw.channels._command_handler.asyncio.gather", new_callable=AsyncMock):
            await handler.handle(msg)

        bg_task.cancel.assert_called_once()
        call_args = params.bus.publish_outbound.call_args
        assert "Stopped 1 task(s)" in call_args[0][0].content

    async def test_skips_already_done_tasks(self):
        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True
        running_task = MagicMock(spec=asyncio.Task)
        running_task.done.return_value = False
        running_task.cancel.return_value = True

        params = _make_params(active_tasks={"sess1": [done_task, running_task]})
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/stop")

        with patch("trpc_agent_sdk.server.openclaw.channels._command_handler.asyncio.gather", new_callable=AsyncMock):
            await handler.handle(msg)

        done_task.cancel.assert_not_called()
        running_task.cancel.assert_called_once()
        call_args = params.bus.publish_outbound.call_args
        assert "Stopped 1 task(s)" in call_args[0][0].content

    async def test_removes_tasks_from_dicts(self):
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        task.cancel.return_value = True

        active = {"sess1": [task]}
        bg = {"sess1": [task]}
        params = _make_params(active_tasks=active, background_tasks=bg)
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/stop")

        with patch("trpc_agent_sdk.server.openclaw.channels._command_handler.asyncio.gather", new_callable=AsyncMock):
            await handler.handle(msg)

        assert "sess1" not in active
        assert "sess1" not in bg


# ---------------------------------------------------------------------------
# _handle_new
# ---------------------------------------------------------------------------
class TestHandleNew:

    async def test_new_session_started(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/new")

        await handler.handle(msg)

        params.session_service.delete_session.assert_awaited_once()
        last_publish = params.bus.publish_outbound.call_args_list[-1]
        assert "New session started" in last_publish[0][0].content

    async def test_new_calls_stop_first(self):
        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        task.cancel.return_value = True

        params = _make_params(active_tasks={"sess1": [task]})
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/new")

        with patch("trpc_agent_sdk.server.openclaw.channels._command_handler.asyncio.gather", new_callable=AsyncMock):
            await handler.handle(msg)

        task.cancel.assert_called_once()

    async def test_new_delete_session_failure(self):
        params = _make_params()
        params.session_service.delete_session = AsyncMock(side_effect=RuntimeError("db error"))
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/new")

        await handler.handle(msg)

        last_publish = params.bus.publish_outbound.call_args_list[-1]
        assert "Failed" in last_publish[0][0].content

    async def test_new_uses_sender_id_when_present(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/new", sender_id="custom_user")

        await handler.handle(msg)
        call_kwargs = params.session_service.delete_session.call_args[1]
        assert call_kwargs["user_id"] == "custom_user"

    async def test_new_uses_default_user_id_when_sender_absent(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/new", sender_id="")

        await handler.handle(msg)
        call_kwargs = params.session_service.delete_session.call_args[1]
        assert "trpc_claw_user" in call_kwargs["user_id"]


# ---------------------------------------------------------------------------
# _handle_help
# ---------------------------------------------------------------------------
class TestHandleHelp:

    async def test_publishes_help_text(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/help")

        await handler.handle(msg)
        call_args = params.bus.publish_outbound.call_args
        content = call_args[0][0].content
        assert "/new" in content
        assert "/stop" in content
        assert "/help" in content
        assert "/restart" in content
        assert "/quit" in content


# ---------------------------------------------------------------------------
# _handle_quit
# ---------------------------------------------------------------------------
class TestHandleQuit:

    async def test_publishes_bye(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/quit")

        await handler.handle(msg)
        call_args = params.bus.publish_outbound.call_args
        assert call_args[0][0].content == "Bye."


# ---------------------------------------------------------------------------
# _handle_restart
# ---------------------------------------------------------------------------
class TestHandleRestart:

    async def test_restart_publishes_message(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/restart")

        with patch("trpc_agent_sdk.server.openclaw.channels._command_handler.asyncio.create_task"):
            await handler.handle(msg)

        call_args = params.bus.publish_outbound.call_args
        assert "Restarting" in call_args[0][0].content
        assert handler.restarting is True

    async def test_restart_guard_prevents_double_restart(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        handler.restarting = True
        msg = _make_msg("/restart")

        await handler.handle(msg)

        call_args = params.bus.publish_outbound.call_args
        assert "already in progress" in call_args[0][0].content

    async def test_restart_creates_task(self):
        params = _make_params()
        handler = TrpcClawCommandHandler(params)
        msg = _make_msg("/restart")

        with patch("trpc_agent_sdk.server.openclaw.channels._command_handler.asyncio.create_task") as mock_ct:
            await handler.handle(msg)
            mock_ct.assert_called_once()
