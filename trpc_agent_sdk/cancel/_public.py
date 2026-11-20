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

Shared memory is enabled by calling :func:`init_shared_mem` before the service
starts and is torn down with :func:`destroy_shared_mem` before the process
exits. The active manager is chosen at that point and is not switched during
normal operation.
"""

from __future__ import annotations

import asyncio
from typing import List
from typing import Optional

from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from . import _shared_mem
from ._local_manager import _LocalManager
from ._shared_manager import _SharedManager
from ._types import SessionKey
from ._types import SharedMemoryConfig
from ._types import SteeringStatus

__all__ = [
    "cancel_run",
    "consume_steering",
    "destroy_shared_mem",
    "get_cancel_event",
    "init_shared_mem",
    "mark_steering_content",
    "raise_if_cancelled",
    "register_run",
    "send_steering",
    "unregister_run",
]

_manager: _LocalManager | _SharedManager = _LocalManager()

_STEERING_PREFIX = "[USER STEERING — sent while the task was running]"
_STEERING_SUFFIX = "[/USER STEERING]"


def register_run(key: SessionKey) -> None:
    """Register one run. Framework lifecycle API, used by ``Runner``."""
    _manager.register_run(key)


def unregister_run(key: SessionKey) -> None:
    """Release one run. Framework lifecycle API, used by ``Runner``."""
    _manager.unregister_run(key)


async def cancel_run(key: SessionKey, timeout: float = 1.0) -> bool:
    """Request cancellation for ``key``.

    Local runs wait up to ``timeout`` for the Runner to finish cleanup. Shared
    runs publish the cancel flag and return immediately; the owner's cleanup is
    not awaited.
    """
    return await _manager.cancel_run(key, timeout=timeout)


async def send_steering(key: SessionKey, content: Content) -> SteeringStatus:
    """Enqueue one steering message for ``key``."""
    return _manager.send_steering(key, content)


async def consume_steering(key: SessionKey) -> Optional[Content]:
    """Consume all pending steering messages and merge them into one Content.

    Returns ``None`` when nothing is pending. Parts are kept in FIFO order and
    the merged Content always has role ``"user"``.
    """
    contents = _manager.drain_steering(key)
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


async def raise_if_cancelled(key: SessionKey) -> None:
    """Raise ``RunCancelledException`` when ``key`` has been cancelled."""
    _manager.raise_if_cancelled(key)


async def get_cancel_event(key: SessionKey) -> Optional[asyncio.Event]:
    """Return the cancellation event for ``key``, if this process owns the run.

    In shared-memory mode, the first call for a run starts a per-run watcher
    that mirrors the shared cancel flag into the local event.
    """
    return _manager.get_cancel_event(key)


def init_shared_mem(config: Optional[SharedMemoryConfig] = None) -> None:
    """Create the shared segment and switch this process to shared control.

    Must be called before the service starts and before any run is registered.
    """
    global _manager

    if isinstance(_manager, _SharedManager) and not _manager._mem.closed:
        if config is not None and config != _manager._mem.config:
            raise ValueError("init_shared_mem() was already called with a different config")
        return

    mem = _shared_mem.init_shared_mem(config)
    _manager = _SharedManager(mem)


def destroy_shared_mem() -> None:
    """Destroy the shared segment and switch back to local control.

    Must be called after all runs finish and before the process exits.
    """
    global _manager

    if not isinstance(_manager, _SharedManager):
        return

    mem = _manager._mem
    _manager.close()
    _shared_mem.destroy_shared_mem(mem)
    _manager = _LocalManager()
