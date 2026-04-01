# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from trpc_agent_sdk.utils import AsyncClosingContextManager


class TestAsyncClosingContextManager:
    """Test suite for AsyncClosingContextManager class."""

    async def test_context_manager_basic(self):
        """Test basic async context manager functionality."""

        async def test_generator():
            yield "test_value"
            yield "another_value"

        gen = test_generator()
        async with AsyncClosingContextManager(gen) as manager:
            assert manager == gen
            # Verify we can iterate
            value = await manager.__anext__()
            assert value == "test_value"

    async def test_context_manager_closes_generator(self):
        """Test that context manager closes the generator on exit."""
        closed = []

        async def test_generator():
            try:
                yield "test"
            finally:
                closed.append(True)

        gen = test_generator()
        async with AsyncClosingContextManager(gen) as manager:
            # Start the generator to ensure finally block will execute
            await manager.__anext__()

        # Verify generator was closed
        assert len(closed) == 1

    async def test_context_manager_with_exception(self):
        """Test that context manager closes generator even on exception."""
        closed = []

        async def test_generator():
            try:
                yield "test"
            finally:
                closed.append(True)

        gen = test_generator()
        try:
            async with AsyncClosingContextManager(gen) as manager:
                # Start the generator to ensure finally block will execute
                await manager.__anext__()
                raise ValueError("test exception")
        except ValueError:
            pass

        # Verify generator was closed even after exception
        assert len(closed) == 1

    async def test_context_manager_returns_generator(self):
        """Test that context manager returns the generator."""

        async def test_generator():
            yield "test"

        gen = test_generator()
        async with AsyncClosingContextManager(gen) as manager:
            assert manager is gen
