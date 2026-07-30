"""Unit tests for trpc_agent_sdk.server.openclaw.tools.message module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.openclaw.tools.message import (
    MESSAGE_CALLBACK_KEY,
    MESSAGE_CHANNEL_KEY,
    MESSAGE_CHAT_ID_KEY,
    MESSAGE_ID_KEY,
    MESSAGE_SENT_IN_TURN_KEY,
    MessageTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_context(
    channel="telegram",
    chat_id="user-42",
    message_id="msg-1",
    callback=None,
):
    ctx = MagicMock(spec=InvocationContext)
    agent_ctx = MagicMock()

    def _get_metadata(key, default=None):
        mapping = {
            MESSAGE_CHANNEL_KEY: channel,
            MESSAGE_CHAT_ID_KEY: chat_id,
            MESSAGE_ID_KEY: message_id,
            MESSAGE_CALLBACK_KEY: callback,
        }
        return mapping.get(key, default)

    agent_ctx.get_metadata = MagicMock(side_effect=_get_metadata)
    ctx.agent_context = agent_ctx
    return ctx


# ---------------------------------------------------------------------------
# MessageTool._run_async_impl
# ---------------------------------------------------------------------------


class TestMessageTool:

    def test_declaration(self):
        tool = MessageTool()
        decl = tool._get_declaration()
        assert decl.name == "message"
        assert "content" in decl.parameters.required

    async def test_missing_channel(self):
        tool = MessageTool()
        ctx = _tool_context(channel="", chat_id="user-42", callback=AsyncMock())
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "hello"},
        )
        assert "no delivery context" in result

    async def test_missing_chat_id(self):
        tool = MessageTool()
        ctx = _tool_context(channel="telegram", chat_id="", callback=AsyncMock())
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "hello"},
        )
        assert "no delivery context" in result

    async def test_missing_callback(self):
        tool = MessageTool()
        ctx = _tool_context(channel="telegram", chat_id="user-42", callback=None)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "hello"},
        )
        assert "send callback not configured" in result

    async def test_success(self):
        cb = AsyncMock()
        tool = MessageTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "hello world"},
        )
        assert "Message sent" in result
        assert "telegram:user-42" in result
        cb.assert_awaited_once()
        sent_msg = cb.call_args[0][0]
        assert sent_msg.content == "hello world"
        assert sent_msg.channel == "telegram"
        assert sent_msg.chat_id == "user-42"

    async def test_success_with_media(self):
        cb = AsyncMock()
        tool = MessageTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "photo", "media": ["/path/img.jpg", "/path/doc.pdf"]},
        )
        assert "2 attachments" in result
        sent_msg = cb.call_args[0][0]
        assert sent_msg.media == ["/path/img.jpg", "/path/doc.pdf"]

    async def test_send_error(self):
        cb = AsyncMock(side_effect=RuntimeError("network error"))
        tool = MessageTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "hello"},
        )
        assert "Error sending message" in result
        assert "network error" in result

    async def test_metadata_tracking(self):
        cb = AsyncMock()
        tool = MessageTool()
        ctx = _tool_context(callback=cb)
        await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "test"},
        )
        ctx.agent_context.with_metadata.assert_called_once_with(MESSAGE_SENT_IN_TURN_KEY, True)

    async def test_override_channel_no_metadata_tracking(self):
        cb = AsyncMock()
        tool = MessageTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "test", "channel": "discord"},
        )
        assert "discord:user-42" in result
        ctx.agent_context.with_metadata.assert_not_called()

    async def test_override_chat_id_no_metadata_tracking(self):
        cb = AsyncMock()
        tool = MessageTool()
        ctx = _tool_context(callback=cb)
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "test", "chat_id": "other-user"},
        )
        assert "telegram:other-user" in result
        ctx.agent_context.with_metadata.assert_not_called()

    async def test_no_agent_context(self):
        tool = MessageTool()
        ctx = MagicMock(spec=InvocationContext)
        ctx.agent_context = None
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "hello"},
        )
        assert "no delivery context" in result

    async def test_message_id_in_metadata(self):
        cb = AsyncMock()
        tool = MessageTool()
        ctx = _tool_context(callback=cb, message_id="msg-99")
        await tool._run_async_impl(
            tool_context=ctx,
            args={"content": "test"},
        )
        sent_msg = cb.call_args[0][0]
        assert sent_msg.metadata["message_id"] == "msg-99"

    async def test_name_is_message(self):
        tool = MessageTool()
        assert tool.name == "message"
