# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent types for TRPC Agent framework."""

import asyncio
from typing import Optional

from google.genai.types import Blob
from google.genai.types import Content
from pydantic import BaseModel
from pydantic import ConfigDict


class LiveRequest(BaseModel):
    """Request send to live agents."""

    model_config = ConfigDict(ser_json_bytes='base64', val_json_bytes='base64')
    """The pydantic model config."""

    content: Optional[Content] = None
    """If set, send the content to the model in turn-by-turn mode."""
    blob: Optional[Blob] = None
    """If set, send the blob to the model in realtime mode."""
    close: bool = False
    """If set, close the queue. queue.shutdown() is only supported in Python 3.13+."""


class LiveRequestQueue:
    """Queue used to send LiveRequest in a live(bidirectional streaming) way."""

    def __init__(self):
        # Ensure there's an event loop available in this thread
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Now create the queue (it will use the event loop we just ensured exists)
        self._queue = asyncio.Queue()

    def close(self):
        self._queue.put_nowait(LiveRequest(close=True))

    def send_content(self, content: Content):
        self._queue.put_nowait(LiveRequest(content=content))

    def send_realtime(self, blob: Blob):
        self._queue.put_nowait(LiveRequest(blob=blob))

    def send(self, req: LiveRequest):
        self._queue.put_nowait(req)

    async def get(self) -> LiveRequest:
        return await self._queue.get()


class ActiveStreamingTool(BaseModel):
    """Manages streaming tool related resources during invocation."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='forbid',
    )
    """The pydantic model config."""

    task: Optional[asyncio.Task] = None
    """The active task of this streaming tool."""

    stream: Optional[LiveRequestQueue] = None
    """The active (input) streams of this streaming tool."""
