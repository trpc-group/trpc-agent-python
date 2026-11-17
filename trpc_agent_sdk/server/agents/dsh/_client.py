# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Async bridge over the synchronous DeepSeek Harness HarnessClient."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict

from ._sdk import HarnessClient
from ._sdk import HarnessConfig
from ._sdk import InitializeResponse
from ._sdk import Notification

TRANSPORT_CLOSED_METHOD = "__trpc_transport_closed__"


class TrpcPromptResult(BaseModel):
    """Receipt returned by ``session/trpc-prompt``.

    ``jsonl``/``version`` are the runtime's snapshot *after* create/resume and
    *before* the new user message is appended. On the first turn they carry
    the DSH-built session header; on later turns they close the
    persistence-only seed-marker gap that is not published as a
    ``session.event`` notification.
    """

    model_config = ConfigDict(extra="ignore")

    messageId: str
    jsonl: str | None = None
    version: int | None = None


class SnapshotResult(BaseModel):
    """Diagnostic JSONL snapshot returned by ``session/trpc-snapshot``."""

    model_config = ConfigDict(extra="ignore")

    jsonl: str
    version: int


class DshClient:
    """Bridge ``HarnessClient`` notifications into asyncio queues.

    ``HarnessClient`` owns a reader thread for stdout. This class adds one
    daemon thread that drains its notification queue and routes each
    notification to the ``asyncio.Queue`` registered for its ``sessionId``.
    """

    def __init__(self, config: HarnessConfig) -> None:
        self._config = config
        self._client = HarnessClient(config)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._notification_thread: threading.Thread | None = None
        self._subscribers: dict[str, asyncio.Queue[Notification]] = {}
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._client.start()
        self._notification_thread = threading.Thread(
            target=self._notification_loop,
            name="dsh-notification-dispatch",
            daemon=True,
        )
        self._notification_thread.start()
        self._started = True

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        response_model: type[BaseModel],
        timeout_seconds: float | None = None,
    ) -> Any:
        if not self._started:
            raise RuntimeError("DshClient is not started")
        return await asyncio.to_thread(
            self._client.request,
            method,
            params,
            response_model=response_model,
            timeout_seconds=timeout_seconds,
        )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self._started:
            raise RuntimeError("DshClient is not started")
        self._client.notify(method, params)

    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncIterator[asyncio.Queue[Notification]]:
        queue: asyncio.Queue[Notification] = asyncio.Queue()
        with self._lock:
            if session_id in self._subscribers:
                raise RuntimeError(f"DSH session {session_id!r} already has an active subscriber")
            self._subscribers[session_id] = queue
        try:
            yield queue
        finally:
            with self._lock:
                if self._subscribers.get(session_id) is queue:
                    del self._subscribers[session_id]

    async def close(self) -> None:
        if not self._started:
            return
        self._started = False
        self._closing.set()
        await asyncio.to_thread(self._client.close)
        thread = self._notification_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._notification_thread = None

    async def initialize(
        self,
        *,
        cwd: str,
        model: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> InitializeResponse:
        params: dict[str, Any] = {"cwd": cwd}
        if model is not None:
            params["model"] = model
        result = await self.request(
            "initialize",
            params,
            response_model=InitializeResponse,
            timeout_seconds=timeout_seconds,
        )
        return result

    def _notification_loop(self) -> None:
        client = self._client
        loop = self._loop
        if loop is None:
            return
        while not self._closing.is_set():
            try:
                notification = client.next_notification()
            except BaseException:
                self._notify_transport_closed()
                return
            payload = notification.payload
            session_id = payload.get("sessionId") if isinstance(payload, dict) else None
            if not isinstance(session_id, str):
                continue
            with self._lock:
                queue = self._subscribers.get(session_id)
            if queue is None:
                continue
            try:
                loop.call_soon_threadsafe(queue.put_nowait, notification)
            except RuntimeError:
                return

    def _notify_transport_closed(self) -> None:
        loop = self._loop
        if loop is None:
            return
        with self._lock:
            queues = list(self._subscribers.values())
        closed = Notification(method=TRANSPORT_CLOSED_METHOD, payload={})
        for queue in queues:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, closed)
            except RuntimeError:
                return
