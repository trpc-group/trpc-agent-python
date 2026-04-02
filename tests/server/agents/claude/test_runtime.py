# -*- coding: utf-8 -*-
"""Unit tests for AsyncRuntime and _cancel_all_tasks."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from trpc_agent_sdk.server.agents.claude._runtime import AsyncRuntime, _cancel_all_tasks


class TestAsyncRuntimeInit:
    def test_default_thread_name(self):
        rt = AsyncRuntime()
        assert rt._thread_name == "AsyncRuntime"
        assert rt._loop is None
        assert rt._loop_thread is None

    def test_custom_thread_name(self):
        rt = AsyncRuntime(thread_name="CustomRuntime")
        assert rt._thread_name == "CustomRuntime"


class TestAsyncRuntimeStartShutdown:
    def test_start_creates_event_loop(self):
        rt = AsyncRuntime(thread_name="test_start")
        rt.start()
        try:
            assert rt._loop is not None
            assert rt._loop.is_running()
            assert rt._loop_thread is not None
            assert rt._loop_thread.is_alive()
        finally:
            rt.shutdown()

    def test_shutdown_stops_loop(self):
        rt = AsyncRuntime(thread_name="test_shutdown")
        rt.start()
        rt.shutdown()
        assert rt._loop_thread is not None
        # Thread should have stopped (or at least loop is no longer running)
        time.sleep(0.2)
        assert not rt._loop_thread.is_alive()

    def test_start_sets_loop_ready(self):
        rt = AsyncRuntime(thread_name="test_ready")
        rt.start()
        try:
            assert rt._loop_ready.is_set()
        finally:
            rt.shutdown()


class TestAsyncRuntimeSubmitCoroutine:
    def test_submit_coroutine_runs_and_returns_result(self):
        rt = AsyncRuntime(thread_name="test_submit")
        rt.start()
        try:
            async def coro():
                return 42

            future = rt.submit_coroutine(coro())
            result = future.result(timeout=5.0)
            assert result == 42
        finally:
            rt.shutdown()

    def test_submit_coroutine_propagates_exception(self):
        rt = AsyncRuntime(thread_name="test_submit_exc")
        rt.start()
        try:
            async def failing_coro():
                raise ValueError("test error")

            future = rt.submit_coroutine(failing_coro())
            with pytest.raises(ValueError, match="test error"):
                future.result(timeout=5.0)
        finally:
            rt.shutdown()

    def test_submit_async_sleep(self):
        rt = AsyncRuntime(thread_name="test_sleep")
        rt.start()
        try:
            async def sleepy():
                await asyncio.sleep(0.05)
                return "done"

            future = rt.submit_coroutine(sleepy())
            assert future.result(timeout=5.0) == "done"
        finally:
            rt.shutdown()


class TestAsyncRuntimeEnsureLoop:
    def test_ensure_loop_raises_when_not_started(self):
        rt = AsyncRuntime(thread_name="test_ensure")
        with pytest.raises(RuntimeError, match="event loop not initialized"):
            rt._ensure_loop()

    def test_ensure_loop_returns_loop_when_started(self):
        rt = AsyncRuntime(thread_name="test_ensure_ok")
        rt.start()
        try:
            loop = rt._ensure_loop()
            assert loop is not None
            assert loop is rt._loop
        finally:
            rt.shutdown()

    def test_shutdown_raises_when_not_started(self):
        rt = AsyncRuntime(thread_name="test_shutdown_no_loop")
        with pytest.raises(RuntimeError, match="event loop not initialized"):
            rt.shutdown()


class TestCancelAllTasks:
    def test_cancel_all_tasks_with_no_pending(self):
        loop = asyncio.new_event_loop()
        try:
            _cancel_all_tasks(loop)
        finally:
            loop.close()

    def test_cancel_all_tasks_cancels_pending(self):
        loop = asyncio.new_event_loop()
        cancelled = []

        async def long_running():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        async def setup():
            task = asyncio.ensure_future(long_running())
            await asyncio.sleep(0.05)
            return task

        try:
            task = loop.run_until_complete(setup())
            _cancel_all_tasks(loop)
            assert len(cancelled) == 1
        finally:
            loop.close()


class TestAsyncRuntimeShutdownTimeout:
    def test_shutdown_warns_on_timeout(self):
        rt = AsyncRuntime(thread_name="test_timeout")
        rt.start()

        # Mock the thread to simulate it staying alive after join
        original_thread = rt._loop_thread
        with patch.object(original_thread, "join"):
            with patch.object(original_thread, "is_alive", return_value=True):
                with patch("trpc_agent_sdk.server.agents.claude._runtime.logger") as mock_logger:
                    loop = rt._loop
                    loop.call_soon_threadsafe(loop.stop)
                    rt.shutdown()
                    mock_logger.warning.assert_called()

        # Actually shut down the runtime
        if original_thread.is_alive():
            if rt._loop and rt._loop.is_running():
                rt._loop.call_soon_threadsafe(rt._loop.stop)
            original_thread.join(timeout=2.0)
