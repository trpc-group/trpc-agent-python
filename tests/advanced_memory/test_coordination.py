"""Tests for Advanced Memory cross-event-loop coordination."""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.advanced_memory.coordination import CrossLoopLock


@pytest.mark.asyncio
async def test_cross_loop_lock_timeout_is_cancellable() -> None:
    """A waiter timeout must not wait for the current lock owner."""
    lock = CrossLoopLock()
    await lock.acquire()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lock.acquire(), timeout=0.02)

    lock.release()
    assert await asyncio.wait_for(lock.acquire(), timeout=0.1)
    lock.release()


@pytest.mark.asyncio
async def test_cross_loop_lock_can_be_reused_after_waiter_cancellation() -> None:
    """Cancelling a waiter must not leave the lock permanently acquired."""
    lock = CrossLoopLock()
    await lock.acquire()
    waiter = asyncio.create_task(lock.acquire())

    await asyncio.sleep(0.02)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    lock.release()
    assert await asyncio.wait_for(lock.acquire(), timeout=0.1)
    lock.release()
