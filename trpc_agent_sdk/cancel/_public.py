# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public cancel and steering functions backed by the active manager.

Agents normally reach these functions through ``InvocationContext``.
:func:`mark_steering_content` is a helper for agent implementations that
consume steering and deliver the marked Content as a hidden event. The
``register_run`` / ``unregister_run`` pair is the framework lifecycle used by
``Runner`` and is not part of the agent-facing API.

Unix socket mode is enabled by calling :func:`init_unix_socket` before
forking workers and is torn down with :func:`release_unix_socket` in the master
before it exits. A custom backend can be installed once with
:func:`set_cancel_manager` before any run starts and before forking workers.
The active manager is chosen by the initialization call.
"""

from __future__ import annotations

import asyncio
import os
from typing import List
from typing import Optional

from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._base import CancelManager
from ._local_manager import _LocalManager
from ._types import SessionKey
from ._types import Status
from ._unix_socket import UnixSocketConfig
from ._unix_socket import _UnixSocketManager
from ._unix_socket import _UnixSocketServer

__all__ = [
    "cancel_run",
    "check_cancel",
    "consume_steering",
    "get_cancel_event",
    "init_unix_socket",
    "mark_steering_content",
    "register_run",
    "release_unix_socket",
    "send_steering",
    "set_cancel_manager",
    "unregister_run",
]

_manager: CancelManager = _LocalManager()
_unix_server: Optional[_UnixSocketServer] = None

_STEERING_PREFIX = "[USER STEERING — sent while the task was running]"
_STEERING_SUFFIX = "[/USER STEERING]"


def set_cancel_manager(manager: CancelManager) -> None:
    """Install a custom cancel and steering backend.

    Call once before any run starts and before forking workers. The instance is
    inherited by forked workers; create process-local resources lazily.
    """
    global _manager
    _manager = manager


async def register_run(key: SessionKey) -> None:
    """Register one run. Framework lifecycle API, used by ``Runner``."""
    await _manager.register_run(key)


async def unregister_run(key: SessionKey) -> None:
    """Release one run. Framework lifecycle API, used by ``Runner``."""
    await _manager.unregister_run(key)


async def cancel_run(key: SessionKey, timeout: float = 1.0) -> Status:
    """Request cancellation for ``key`` and wait for cleanup.

    ``timeout`` is supplied by ``Runner.cancel_run_async`` and bounds how long
    the active manager waits for the owner to finish cleanup. ``_LocalManager``
    waits on ``cleanup_event``; ``_UnixSocketManager`` polls the master's
    ``is_running`` at ``poll_interval`` until the run is released.

    Returns ``Status(ok=True)`` once cleanup is confirmed. Returns
    ``Status(ok=False, error_msg=...)`` when no active run matches, the request
    could not be delivered, or the wait expires. A timeout does not mean the
    cancellation failed: the signal has already been delivered and the run may
    stop shortly after.
    """
    return await _manager.cancel_run(key, timeout=timeout)


async def send_steering(key: SessionKey, content: Content) -> Status:
    """Enqueue one steering message for ``key``."""
    return await _manager.send_steering(key, content)


async def consume_steering(key: SessionKey) -> Optional[Content]:
    """Consume all pending steering messages and merge them into one Content.

    Returns ``None`` when nothing is pending. Parts are kept in FIFO order and
    the merged Content always has role ``"user"``.
    """
    contents = await _manager.drain_steering(key)
    if not contents:
        return None
    parts = [part for content in contents for part in (content.parts or [])]
    return Content(role="user", parts=parts)


def mark_steering_content(content: Content) -> Content:
    """Return a copy of ``content`` with steering markers around its parts.

    Agent implementations wrap the returned Content in a ``visible=False``
    Event and yield it. Runner persists the event without exposing it to the
    client, so later model requests can read it from session history.
    """
    parts: List[Part] = [Part(text=_STEERING_PREFIX)]
    parts.extend(content.parts or [])
    parts.append(Part(text=_STEERING_SUFFIX))
    return content.model_copy(update={"parts": parts})


async def check_cancel(key: SessionKey) -> bool:
    """Return whether ``key`` has been cancelled."""
    return await _manager.check_cancel(key)


async def get_cancel_event(key: SessionKey) -> Optional[asyncio.Event]:
    """Return the cancellation event for ``key``, if this process owns the run.

    In Unix socket mode, a per-run poller keeps the event in sync with the
    master.
    """
    return await _manager.get_cancel_event(key)


def init_unix_socket(config: Optional[UnixSocketConfig] = None) -> None:
    """Start the Unix socket server in the master process.

    Must be called before forking workers. Workers inherit the active manager
    and use it to manage their own runs.
    """
    global _manager, _unix_server

    config = config or UnixSocketConfig()
    server = _UnixSocketServer(config)
    server.start()
    _unix_server = server
    _manager = _UnixSocketManager(config)


def release_unix_socket() -> None:
    """Stop the Unix socket server. Must be called in the master process."""
    global _manager, _unix_server

    server = _unix_server
    if server is None:
        return
    if server.owner_pid != os.getpid():
        raise RuntimeError("release_unix_socket() must be called in the master process")

    try:
        server.stop()
    finally:
        _unix_server = None
        _manager = _LocalManager()
