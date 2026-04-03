# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""WeCom channel patch for proper streaming behavior."""

from __future__ import annotations

from nanobot.bus.events import OutboundMessage
from nanobot.channels.manager import ChannelManager
from nanobot.channels.wecom import WecomChannel as NanobotWecomChannel
from trpc_agent_sdk.log import logger

from ._repair import register_channel_repair


class WecomChannel(NanobotWecomChannel):
    """WeCom channel with progress streaming support."""

    def __init__(self, config, bus):
        stream_reply = True
        if isinstance(config, dict):
            stream_reply = bool(config.get("stream_reply", True))
        else:
            stream_reply = bool(getattr(config, "stream_reply", True))
        super().__init__(config, bus)
        self._stream_reply = stream_reply
        # Correlation key -> stream id
        self._active_stream_ids: dict[str, str] = {}

    def _stream_key(self, msg: OutboundMessage) -> str:
        message_id = ""
        if msg.metadata:
            message_id = str(msg.metadata.get("message_id", "") or "")
        if message_id:
            return f"{msg.chat_id}:{message_id}"
        return str(msg.chat_id)

    async def send(self, msg: OutboundMessage) -> None:
        """Send message to WeCom with incremental stream chunks."""
        if not self._client:
            logger.warning("WeCom client not initialized")
            return

        content = (msg.content or "").strip()
        if not content:
            return

        frame = self._chat_frames.get(msg.chat_id)
        if not frame:
            logger.warning("No frame found for chat {}, cannot reply", msg.chat_id)
            return

        key = self._stream_key(msg)
        is_progress = bool((msg.metadata or {}).get("_progress"))
        if is_progress and not self._stream_reply:
            return

        stream_id = self._active_stream_ids.get(key)
        if not stream_id:
            stream_id = self._generate_req_id("stream")
            self._active_stream_ids[key] = stream_id

        # Progress chunk keeps stream open; final normal message closes it.
        await self._client.reply_stream(
            frame,
            stream_id,
            content,
            finish=not self._stream_reply,
        )

        if not is_progress:
            self._active_stream_ids.pop(key, None)


def repair_wecom_channel(name: str, channel_manager: ChannelManager) -> None:
    """Replace default WeCom channel with streaming-capable channel."""
    section = getattr(channel_manager.config.channels, name, None)
    if not section:
        return
    enabled = (section.get("enabled", False) if isinstance(section, dict) else getattr(section, "enabled", False))
    if not enabled:
        return
    channel_manager.channels[name] = WecomChannel(section, channel_manager.bus)


register_channel_repair("wecom", repair_wecom_channel)
