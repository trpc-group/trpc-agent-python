# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw.channels._wecom."""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def _ensure_nanobot_stubs():
    """Insert lightweight nanobot stubs so _wecom.py can be imported."""
    if "nanobot" in sys.modules and hasattr(sys.modules["nanobot"], "__path__"):
        return  # real nanobot is installed, nothing to do

    def _make_mod(name, parent=None):
        mod = types.ModuleType(name)
        mod.__path__ = []  # mark as package
        mod.__package__ = name
        sys.modules[name] = mod
        if parent is not None:
            setattr(parent, name.rsplit(".", 1)[-1], mod)
        return mod

    nanobot = _make_mod("nanobot")
    bus = _make_mod("nanobot.bus", nanobot)
    events = _make_mod("nanobot.bus.events", bus)
    queue = _make_mod("nanobot.bus.queue", bus)
    channels = _make_mod("nanobot.channels", nanobot)
    mgr = _make_mod("nanobot.channels.manager", channels)
    wecom = _make_mod("nanobot.channels.wecom", channels)
    telegram = _make_mod("nanobot.channels.telegram", channels)
    utils = _make_mod("nanobot.utils", nanobot)
    helpers = _make_mod("nanobot.utils.helpers", utils)
    config = _make_mod("nanobot.config", nanobot)
    schema = _make_mod("nanobot.config.schema", config)
    loader = _make_mod("nanobot.config.loader", config)
    cron = _make_mod("nanobot.cron", nanobot)
    cron_service = _make_mod("nanobot.cron.service", cron)
    cron_types = _make_mod("nanobot.cron.types", cron)
    heartbeat = _make_mod("nanobot.heartbeat", nanobot)

    # Stub types used by production code
    _BaseWecomChannel = type("WecomChannel", (), {
        "__init__": lambda self, config, bus: None,
        "_client": None,
        "_chat_frames": {},
        "_generate_req_id": lambda self, prefix: f"{prefix}-001",
    })
    wecom.WecomChannel = _BaseWecomChannel

    _BaseTelegramChannel = type("TelegramChannel", (), {"__init__": lambda self, config, bus: None})
    telegram.TelegramChannel = _BaseTelegramChannel

    mgr.ChannelManager = type("ChannelManager", (), {})
    events.OutboundMessage = type("OutboundMessage", (), {})
    events.InboundMessage = type("InboundMessage", (), {})
    queue.MessageBus = type("MessageBus", (), {})
    helpers.detect_image_mime = lambda *a, **k: "image/png"
    helpers.ensure_dir = lambda *a, **k: None
    helpers.safe_filename = lambda s: s
    schema.Config = type("Config", (), {})
    schema.AgentDefaults = type("AgentDefaults", (), {})
    schema.WebSearchConfig = type("WebSearchConfig", (), {})
    loader.set_config_path = lambda *a, **k: None
    cron_service.CronService = type("CronService", (), {})
    cron_types.CronSchedule = type("CronSchedule", (), {})
    cron_types.CronJob = type("CronJob", (), {})


_ensure_nanobot_stubs()

from trpc_agent_sdk.server.openclaw.channels._wecom import (  # noqa: E402
    WecomChannel,
    repair_wecom_channel,
)


def _make_msg(chat_id="chat1", content="hello", metadata=None):
    msg = MagicMock()
    msg.chat_id = chat_id
    msg.content = content
    msg.metadata = metadata
    return msg


# ---------------------------------------------------------------------------
# WecomChannel.__init__
# ---------------------------------------------------------------------------
class TestWecomChannelInit:

    def test_dict_config_default_stream_reply(self):
        ch = WecomChannel({"key": "val"}, bus=MagicMock())
        assert ch._stream_reply is True

    def test_dict_config_stream_reply_false(self):
        ch = WecomChannel({"stream_reply": False}, bus=MagicMock())
        assert ch._stream_reply is False

    def test_dict_config_stream_reply_explicit_true(self):
        ch = WecomChannel({"stream_reply": True}, bus=MagicMock())
        assert ch._stream_reply is True

    def test_object_config_default_stream_reply(self):
        config = MagicMock(spec=[])
        ch = WecomChannel(config, bus=MagicMock())
        assert ch._stream_reply is True

    def test_object_config_stream_reply_false(self):
        config = MagicMock()
        config.stream_reply = False
        ch = WecomChannel(config, bus=MagicMock())
        assert ch._stream_reply is False

    def test_active_stream_ids_initialized_empty(self):
        ch = WecomChannel({}, bus=MagicMock())
        assert ch._active_stream_ids == {}


# ---------------------------------------------------------------------------
# WecomChannel._stream_key
# ---------------------------------------------------------------------------
class TestStreamKey:

    def test_with_message_id(self):
        ch = WecomChannel({}, bus=MagicMock())
        msg = _make_msg(chat_id="c1", metadata={"message_id": "m123"})
        assert ch._stream_key(msg) == "c1:m123"

    def test_without_message_id(self):
        ch = WecomChannel({}, bus=MagicMock())
        msg = _make_msg(chat_id="c2", metadata={})
        assert ch._stream_key(msg) == "c2"

    def test_none_metadata(self):
        ch = WecomChannel({}, bus=MagicMock())
        msg = _make_msg(chat_id="c3", metadata=None)
        assert ch._stream_key(msg) == "c3"

    def test_empty_message_id_string(self):
        ch = WecomChannel({}, bus=MagicMock())
        msg = _make_msg(chat_id="c4", metadata={"message_id": ""})
        assert ch._stream_key(msg) == "c4"

    def test_none_message_id_value(self):
        ch = WecomChannel({}, bus=MagicMock())
        msg = _make_msg(chat_id="c5", metadata={"message_id": None})
        assert ch._stream_key(msg) == "c5"


# ---------------------------------------------------------------------------
# WecomChannel.send
# ---------------------------------------------------------------------------
class TestWecomChannelSend:

    async def test_no_client_returns_early(self):
        ch = WecomChannel({}, bus=MagicMock())
        ch._client = None
        await ch.send(_make_msg())

    async def test_empty_content_returns_early(self):
        ch = WecomChannel({}, bus=MagicMock())
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        await ch.send(_make_msg(content="   "))
        ch._client.reply_stream.assert_not_called()

    async def test_no_frame_returns_early(self):
        ch = WecomChannel({}, bus=MagicMock())
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        ch._chat_frames = {}
        await ch.send(_make_msg(chat_id="unknown"))
        ch._client.reply_stream.assert_not_called()

    async def test_progress_with_stream_reply_disabled_returns_early(self):
        ch = WecomChannel({"stream_reply": False}, bus=MagicMock())
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        ch._chat_frames = {"chat1": MagicMock()}
        await ch.send(_make_msg(chat_id="chat1", metadata={"_progress": True}))
        ch._client.reply_stream.assert_not_called()

    async def test_new_stream_created(self):
        ch = WecomChannel({}, bus=MagicMock())
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        ch._generate_req_id = MagicMock(return_value="stream-new-001")
        frame = MagicMock()
        ch._chat_frames = {"chat1": frame}

        await ch.send(_make_msg(chat_id="chat1", content="hi", metadata={}))

        ch._client.reply_stream.assert_awaited_once()
        call_args = ch._client.reply_stream.call_args
        assert call_args[0][0] is frame
        assert call_args[0][2] == "hi"

    async def test_existing_stream_reused(self):
        ch = WecomChannel({}, bus=MagicMock())
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        ch._chat_frames = {"chat1": MagicMock()}
        ch._active_stream_ids = {"chat1": "existing-stream-id"}

        await ch.send(_make_msg(chat_id="chat1", content="more", metadata={"_progress": True}))

        call_args = ch._client.reply_stream.call_args
        assert call_args[0][1] == "existing-stream-id"

    async def test_final_message_clears_stream(self):
        ch = WecomChannel({}, bus=MagicMock())
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        ch._chat_frames = {"chat1": MagicMock()}
        ch._active_stream_ids = {"chat1": "stream-123"}

        await ch.send(_make_msg(chat_id="chat1", content="done", metadata={}))

        assert "chat1" not in ch._active_stream_ids

    async def test_progress_message_keeps_stream(self):
        ch = WecomChannel({}, bus=MagicMock())
        ch._client = MagicMock()
        ch._client.reply_stream = AsyncMock()
        ch._chat_frames = {"chat1": MagicMock()}
        ch._active_stream_ids = {"chat1": "stream-456"}

        await ch.send(_make_msg(chat_id="chat1", content="partial", metadata={"_progress": True}))

        assert "chat1" in ch._active_stream_ids


# ---------------------------------------------------------------------------
# repair_wecom_channel
# ---------------------------------------------------------------------------
class TestRepairWecomChannel:

    def test_no_section_returns_early(self):
        mgr = MagicMock()
        mgr.config.channels = MagicMock(spec=[])
        mgr.channels = {}

        repair_wecom_channel("wecom", mgr)
        assert "wecom" not in mgr.channels

    def test_not_enabled_dict_returns_early(self):
        section = {"enabled": False}
        mgr = MagicMock()
        setattr(mgr.config.channels, "wecom", section)
        mgr.channels = {}

        repair_wecom_channel("wecom", mgr)
        assert "wecom" not in mgr.channels

    def test_not_enabled_object_returns_early(self):
        section = MagicMock()
        section.enabled = False
        mgr = MagicMock()
        setattr(mgr.config.channels, "wecom", section)
        mgr.channels = {}

        repair_wecom_channel("wecom", mgr)
        assert "wecom" not in mgr.channels

    def test_enabled_dict_replaces_channel(self):
        section = {"enabled": True, "stream_reply": True}
        mgr = MagicMock()
        mgr.channels = {}
        setattr(mgr.config.channels, "wecom", section)

        repair_wecom_channel("wecom", mgr)
        assert "wecom" in mgr.channels
        assert isinstance(mgr.channels["wecom"], WecomChannel)

    def test_enabled_object_replaces_channel(self):
        section = MagicMock()
        section.enabled = True
        section.stream_reply = True
        mgr = MagicMock()
        mgr.channels = {}
        setattr(mgr.config.channels, "mywecom", section)

        repair_wecom_channel("mywecom", mgr)
        assert "mywecom" in mgr.channels
        assert isinstance(mgr.channels["mywecom"], WecomChannel)
