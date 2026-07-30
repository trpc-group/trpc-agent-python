"""Unit tests for trpc_agent_sdk.server.openclaw.tools.spawn_task module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.openclaw.tools.spawn_task import (
    SPAWN_TASK_CHANNEL_KEY,
    SPAWN_TASK_CHAT_ID_KEY,
    SPAWN_TASK_SESSION_KEY,
    SPAWN_TASK_SUBMIT_CALLBACK_KEY,
    SPAWN_TASK_USER_ID_KEY,
    SpawnTaskTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_context(
    callback=None,
    channel="telegram",
    chat_id="user-1",
    session_key="sess-1",
    user_id="u1",
):
    ctx = MagicMock(spec=InvocationContext)
    agent_ctx = MagicMock()

    def _get_metadata(key, default=None):
        mapping = {
            SPAWN_TASK_SUBMIT_CALLBACK_KEY: callback,
            SPAWN_TASK_CHANNEL_KEY: channel,
            SPAWN_TASK_CHAT_ID_KEY: chat_id,
            SPAWN_TASK_SESSION_KEY: session_key,
            SPAWN_TASK_USER_ID_KEY: user_id,
        }
        return mapping.get(key, default)

    agent_ctx.get_metadata = MagicMock(side_effect=_get_metadata)
    ctx.agent_context = agent_ctx
    return ctx


# ---------------------------------------------------------------------------
# SpawnTaskTool
# ---------------------------------------------------------------------------


class TestSpawnTaskTool:

    def test_declaration(self):
        tool = SpawnTaskTool()
        decl = tool._get_declaration()
        assert decl.name == "spawn_task"
        assert "task" in decl.parameters.required

    def test_name(self):
        tool = SpawnTaskTool()
        assert tool.name == "spawn_task"

    async def test_missing_callback(self):
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=None)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "do something"},
        )
        assert "spawn callback not configured" in result

    async def test_empty_task(self):
        cb = MagicMock(return_value="ok")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"task": ""},
        )
        assert "task is required" in result

    async def test_missing_runtime_context(self):
        cb = MagicMock(return_value="ok")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb, channel="", chat_id="user-1", session_key="sess-1")
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "work"},
        )
        assert "missing runtime context" in result

    async def test_missing_session_key(self):
        cb = MagicMock(return_value="ok")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb, channel="telegram", chat_id="user-1", session_key="")
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "work"},
        )
        assert "missing runtime context" in result

    async def test_sync_callback(self):
        cb = MagicMock(return_value="task-id-42")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "do the work"},
        )
        assert result == "task-id-42"
        cb.assert_called_once()
        call_kwargs = cb.call_args[1]
        assert call_kwargs["task"] == "do the work"
        assert call_kwargs["origin_channel"] == "telegram"
        assert call_kwargs["origin_chat_id"] == "user-1"
        assert call_kwargs["session_key"] == "sess-1"
        assert call_kwargs["user_id"] == "u1"

    async def test_async_callback(self):
        cb = AsyncMock(return_value="async-task-id")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "async work"},
        )
        assert result == "async-task-id"
        cb.assert_called_once()

    async def test_label_forwarded(self):
        cb = MagicMock(return_value="ok")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb)
        await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "work", "label": "my-label"},
        )
        call_kwargs = cb.call_args[1]
        assert call_kwargs["label"] == "my-label"

    async def test_no_agent_context(self):
        tool = SpawnTaskTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent_context = None
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "work"},
        )
        assert "spawn callback not configured" in result

    async def test_override_channel_and_chat_id(self):
        cb = MagicMock(return_value="ok")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb)
        await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "work", "channel": "discord", "chat_id": "other-user"},
        )
        call_kwargs = cb.call_args[1]
        assert call_kwargs["origin_channel"] == "discord"
        assert call_kwargs["origin_chat_id"] == "other-user"

    async def test_default_user_id_when_empty(self):
        cb = MagicMock(return_value="ok")
        tool = SpawnTaskTool()
        ctx = _tool_context(callback=cb, user_id="")
        await tool._run_async_impl(
            tool_context=ctx,
            args={"task": "work"},
        )
        call_kwargs = cb.call_args[1]
        assert call_kwargs["user_id"] == "user"
