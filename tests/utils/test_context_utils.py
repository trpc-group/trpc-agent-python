# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.utils._context_utils.

Covers:
- AsyncClosingContextManager: __aenter__, __aexit__, aclose delegation,
  exception safety, isinstance check against AbstractAsyncContextManager
"""

from contextlib import AbstractAsyncContextManager

from trpc_agent_sdk.utils import AsyncClosingContextManager


class TestAsyncClosingContextManager:
    """Test suite for AsyncClosingContextManager class."""

    async def test_basic_enter_returns_generator(self):
        async def gen():
            yield "value"

        g = gen()
        async with AsyncClosingContextManager(g) as manager:
            assert manager is g

    async def test_iterate_values(self):
        async def gen():
            yield "first"
            yield "second"

        g = gen()
        async with AsyncClosingContextManager(g) as manager:
            val = await manager.__anext__()
            assert val == "first"

    async def test_closes_generator_on_exit(self):
        closed = []

        async def gen():
            try:
                yield "test"
            finally:
                closed.append(True)

        g = gen()
        async with AsyncClosingContextManager(g) as manager:
            await manager.__anext__()

        assert len(closed) == 1

    async def test_closes_generator_on_exception(self):
        closed = []

        async def gen():
            try:
                yield "test"
            finally:
                closed.append(True)

        g = gen()
        try:
            async with AsyncClosingContextManager(g) as manager:
                await manager.__anext__()
                raise ValueError("boom")
        except ValueError:
            pass

        assert len(closed) == 1

    async def test_is_abstract_async_context_manager(self):
        async def gen():
            yield 1

        cm = AsyncClosingContextManager(gen())
        assert isinstance(cm, AbstractAsyncContextManager)

    async def test_async_generator_attribute(self):
        async def gen():
            yield 42

        g = gen()
        cm = AsyncClosingContextManager(g)
        assert cm.async_generator is g
        await g.aclose()

    async def test_multiple_yields_closed_early(self):
        """Generator with many yields is properly closed even if not fully consumed."""
        steps = []

        async def gen():
            try:
                for i in range(100):
                    steps.append(i)
                    yield i
            finally:
                steps.append("closed")

        g = gen()
        async with AsyncClosingContextManager(g) as manager:
            await manager.__anext__()
            await manager.__anext__()

        assert steps[-1] == "closed"
        assert len(steps) < 100

    async def test_no_iteration_still_closes(self):
        """Generator is closed even if never iterated (aclose on unstarted gen)."""
        async def gen():
            yield "never consumed"

        g = gen()
        async with AsyncClosingContextManager(g):
            pass
        # After exiting, generator should be closed - iterating raises StopAsyncIteration
        import pytest
        with pytest.raises(StopAsyncIteration):
            await g.__anext__()
