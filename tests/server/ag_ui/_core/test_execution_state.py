# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for _execution_state module."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from trpc_agent_sdk.server.ag_ui._core._execution_state import ExecutionState


def _make_state(task=None, thread_id="thread-1", queue=None):
    if queue is None:
        queue = asyncio.Queue()
    if task is None:
        loop = asyncio.get_event_loop()
        task = loop.create_future()
    return ExecutionState(task=task, thread_id=thread_id, event_queue=queue)


class TestInit:
    async def test_attributes_set(self):
        queue = asyncio.Queue()
        task = asyncio.get_event_loop().create_future()
        state = ExecutionState(task=task, thread_id="t-1", event_queue=queue)
        assert state.task is task
        assert state.thread_id == "t-1"
        assert state.event_queue is queue
        assert state.is_complete is False
        assert state.pending_tool_calls == set()
        assert state.start_time > 0


class TestIsStale:
    async def test_not_stale(self):
        state = _make_state()
        assert state.is_stale(timeout_seconds=9999) is False

    async def test_stale(self):
        state = _make_state()
        state.start_time = time.time() - 100
        assert state.is_stale(timeout_seconds=50) is True

    async def test_exact_boundary(self):
        state = _make_state()
        state.start_time = time.time() - 10
        assert state.is_stale(timeout_seconds=5) is True


class TestCancel:
    async def test_cancel_running_task(self):
        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_running())
        state = ExecutionState(task=task, thread_id="t-1", event_queue=asyncio.Queue())
        assert not task.done()
        await state.cancel()
        assert state.is_complete is True
        assert task.done()

    async def test_cancel_already_done_task(self):
        async def instant():
            return "done"

        task = asyncio.create_task(instant())
        await task
        state = ExecutionState(task=task, thread_id="t-2", event_queue=asyncio.Queue())
        await state.cancel()
        assert state.is_complete is True


class TestGetExecutionTime:
    async def test_positive_value(self):
        state = _make_state()
        assert state.get_execution_time() >= 0

    async def test_increases_over_time(self):
        state = _make_state()
        state.start_time = time.time() - 5
        assert state.get_execution_time() >= 4.9


class TestPendingToolCalls:
    async def test_add_pending(self):
        state = _make_state()
        state.add_pending_tool_call("tc-1")
        assert "tc-1" in state.pending_tool_calls
        assert state.has_pending_tool_calls() is True

    async def test_remove_pending(self):
        state = _make_state()
        state.add_pending_tool_call("tc-1")
        state.remove_pending_tool_call("tc-1")
        assert "tc-1" not in state.pending_tool_calls
        assert state.has_pending_tool_calls() is False

    async def test_remove_nonexistent_does_not_raise(self):
        state = _make_state()
        state.remove_pending_tool_call("nonexistent")
        assert state.has_pending_tool_calls() is False

    async def test_has_pending_tool_calls_empty(self):
        state = _make_state()
        assert state.has_pending_tool_calls() is False

    async def test_multiple_pending(self):
        state = _make_state()
        state.add_pending_tool_call("tc-1")
        state.add_pending_tool_call("tc-2")
        assert state.has_pending_tool_calls() is True
        state.remove_pending_tool_call("tc-1")
        assert state.has_pending_tool_calls() is True
        state.remove_pending_tool_call("tc-2")
        assert state.has_pending_tool_calls() is False


class TestGetStatus:
    async def test_running(self):
        async def hang():
            await asyncio.sleep(100)

        task = asyncio.create_task(hang())
        state = ExecutionState(task=task, thread_id="t", event_queue=asyncio.Queue())
        assert state.get_status() == "running"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_task_done(self):
        async def instant():
            return 42

        task = asyncio.create_task(instant())
        await task
        state = ExecutionState(task=task, thread_id="t", event_queue=asyncio.Queue())
        state.is_complete = False
        assert state.get_status() == "task_done"

    async def test_complete(self):
        async def instant():
            return None

        task = asyncio.create_task(instant())
        await task
        state = ExecutionState(task=task, thread_id="t", event_queue=asyncio.Queue())
        state.is_complete = True
        assert state.get_status() == "complete"

    async def test_complete_awaiting_tools(self):
        async def instant():
            return None

        task = asyncio.create_task(instant())
        await task
        state = ExecutionState(task=task, thread_id="t", event_queue=asyncio.Queue())
        state.is_complete = True
        state.add_pending_tool_call("tc-1")
        assert state.get_status() == "complete_awaiting_tools"


class TestRepr:
    async def test_format(self):
        async def hang():
            await asyncio.sleep(100)

        task = asyncio.create_task(hang())
        state = ExecutionState(task=task, thread_id="t-repr", event_queue=asyncio.Queue())
        r = repr(state)
        assert "ExecutionState(" in r
        assert "thread_id='t-repr'" in r
        assert "status='running'" in r
        assert "runtime=" in r
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_repr_complete(self):
        async def instant():
            return None

        task = asyncio.create_task(instant())
        await task
        state = ExecutionState(task=task, thread_id="t-done", event_queue=asyncio.Queue())
        state.is_complete = True
        r = repr(state)
        assert "status='complete'" in r
