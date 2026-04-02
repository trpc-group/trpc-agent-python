# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tests for trpc_agent_sdk.types._agent_types.

Covers:
    - LiveRequest: construction, defaults, serialisation config
    - LiveRequestQueue: close, send_content, send_realtime, send, async get
    - ActiveStreamingTool: construction, extra-forbid, arbitrary types
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from google.genai.types import Blob, Content, Part
from pydantic import ValidationError

from trpc_agent_sdk.types._agent_types import (
    ActiveStreamingTool,
    LiveRequest,
    LiveRequestQueue,
)


# ---------------------------------------------------------------------------
# LiveRequest
# ---------------------------------------------------------------------------
class TestLiveRequest:
    """Tests for the LiveRequest Pydantic model."""

    def test_default_values(self):
        req = LiveRequest()
        assert req.content is None
        assert req.blob is None
        assert req.close is False

    def test_close_flag(self):
        req = LiveRequest(close=True)
        assert req.close is True

    def test_with_content(self):
        content = Content(parts=[Part(text="hello")])
        req = LiveRequest(content=content)
        assert req.content is not None
        assert req.content.parts[0].text == "hello"

    def test_with_blob(self):
        blob = Blob(data=b"raw", mime_type="audio/pcm")
        req = LiveRequest(blob=blob)
        assert req.blob is not None
        assert req.blob.mime_type == "audio/pcm"

    def test_serialisation_roundtrip(self):
        content = Content(parts=[Part(text="test")])
        req = LiveRequest(content=content, close=True)
        json_str = req.model_dump_json()
        restored = LiveRequest.model_validate_json(json_str)
        assert restored.close is True
        assert restored.content.parts[0].text == "test"

    def test_model_config_json_bytes(self):
        cfg = LiveRequest.model_config
        assert cfg.get("ser_json_bytes") == "base64"
        assert cfg.get("val_json_bytes") == "base64"


# ---------------------------------------------------------------------------
# LiveRequestQueue
# ---------------------------------------------------------------------------
class TestLiveRequestQueue:
    """Tests for the async LiveRequestQueue wrapper."""

    def test_init_creates_queue(self):
        q = LiveRequestQueue()
        assert hasattr(q, "_queue")
        assert isinstance(q._queue, asyncio.Queue)

    async def test_close_sends_close_request(self):
        q = LiveRequestQueue()
        q.close()
        req = await q.get()
        assert isinstance(req, LiveRequest)
        assert req.close is True

    async def test_send_content(self):
        q = LiveRequestQueue()
        content = Content(parts=[Part(text="hi")])
        q.send_content(content)
        req = await q.get()
        assert req.content is not None
        assert req.content.parts[0].text == "hi"
        assert req.close is False

    async def test_send_realtime(self):
        q = LiveRequestQueue()
        blob = Blob(data=b"\x00\x01", mime_type="audio/pcm")
        q.send_realtime(blob)
        req = await q.get()
        assert req.blob is not None
        assert req.blob.mime_type == "audio/pcm"

    async def test_send_raw_request(self):
        q = LiveRequestQueue()
        raw = LiveRequest(close=True)
        q.send(raw)
        req = await q.get()
        assert req is raw

    async def test_fifo_order(self):
        q = LiveRequestQueue()
        q.send(LiveRequest(close=False))
        q.send(LiveRequest(close=True))

        first = await q.get()
        second = await q.get()
        assert first.close is False
        assert second.close is True

    async def test_get_blocks_until_item_available(self):
        q = LiveRequestQueue()
        loop = asyncio.get_running_loop()

        async def _delayed_put():
            await asyncio.sleep(0.05)
            q.send(LiveRequest(close=True))

        loop.create_task(_delayed_put())
        req = await q.get()
        assert req.close is True


# ---------------------------------------------------------------------------
# ActiveStreamingTool
# ---------------------------------------------------------------------------
class TestActiveStreamingTool:
    """Tests for the ActiveStreamingTool Pydantic model."""

    def test_default_values(self):
        tool = ActiveStreamingTool()
        assert tool.task is None
        assert tool.stream is None

    def test_with_task(self):
        mock_task = MagicMock(spec=asyncio.Task)
        tool = ActiveStreamingTool(task=mock_task)
        assert tool.task is mock_task

    def test_with_stream(self):
        q = LiveRequestQueue()
        tool = ActiveStreamingTool(stream=q)
        assert tool.stream is q

    def test_extra_forbid(self):
        with pytest.raises(ValidationError):
            ActiveStreamingTool(unknown_field="nope")

    def test_arbitrary_types_allowed(self):
        cfg = ActiveStreamingTool.model_config
        assert cfg.get("arbitrary_types_allowed") is True
