# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Per-session / per-branch locks for :class:`TaskStore` read-modify-write."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from typing import Dict
from typing import Tuple

from trpc_agent_sdk.context import InvocationContext

from ._helpers import state_key

_LockKey = Tuple[str, str, str, str]
_locks: Dict[_LockKey, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()


def store_lock_key(
    tool_context: InvocationContext,
    *,
    prefix: str,
    branch: str,
) -> _LockKey:
    """Build a stable key for the branch-scoped task board."""
    session = tool_context.session
    return (
        getattr(session, "app_name", "") or "",
        getattr(session, "user_id", "") or "",
        getattr(session, "id", "") or "",
        state_key(prefix, branch),
    )


async def _get_lock(key: _LockKey) -> asyncio.Lock:
    async with _registry_lock:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


@asynccontextmanager
async def task_store_lock(
    tool_context: InvocationContext,
    *,
    prefix: str,
    branch: str,
) -> AsyncIterator[None]:
    """Serialize load / mutate / save for one task board."""
    lock = await _get_lock(store_lock_key(tool_context, prefix=prefix, branch=branch))
    async with lock:
        yield


def reset_locks_for_tests() -> None:
    """Clear the lock registry (tests only)."""
    _locks.clear()
