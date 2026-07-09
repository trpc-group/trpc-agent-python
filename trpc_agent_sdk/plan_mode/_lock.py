# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Per-session locks for PlanRecord read-modify-write."""

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
async def plan_store_lock(
    tool_context: InvocationContext,
    *,
    prefix: str,
    branch: str,
) -> AsyncIterator[None]:
    """Serialize load / mutate / save for one session plan."""
    lock = await _get_lock(store_lock_key(tool_context, prefix=prefix, branch=branch))
    async with lock:
        yield


def release_lock(
    tool_context: InvocationContext,
    *,
    prefix: str,
    branch: str,
) -> None:
    """Drop the registry entry for one session/branch.

    Call once a plan reaches a terminal state (``approved``)
    so the process-global ``_locks`` dict doesn't grow without bound for
    long-running servers with many sessions. Safe to call even if no entry
    exists. Best-effort: a lock actively held by another coroutine keeps
    working via its own reference even after being popped here.
    """
    _locks.pop(store_lock_key(tool_context, prefix=prefix, branch=branch), None)


def reset_locks_for_tests() -> None:
    """Clear the lock registry (tests only)."""
    _locks.clear()
