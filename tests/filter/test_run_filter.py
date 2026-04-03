# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for run_filter module (run_filters, run_stream_filters, adapters)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.abc import FilterResult, FilterType
from trpc_agent_sdk.filter._base_filter import BaseFilter
from trpc_agent_sdk.filter._run_filter import (
    coroutine_handler_adapter,
    run_filters,
    run_stream_filters,
    stream_handler_adapter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class RecordingFilter(BaseFilter):
    """Filter that records calls and passes through."""

    def __init__(self, name: str = "recorder"):
        super().__init__()
        self._name = name
        self.before_calls = 0
        self.after_calls = 0

    async def _before(self, ctx, req, rsp):
        self.before_calls += 1

    async def _after(self, ctx, req, rsp):
        self.after_calls += 1


class ModifyingBeforeFilter(BaseFilter):
    """Filter that modifies the request in _before."""

    def __init__(self, prefix: str):
        super().__init__()
        self._name = f"modify_{prefix}"
        self._prefix = prefix

    async def _before(self, ctx, req, rsp):
        req["trace"].append(f"before_{self._prefix}")

    async def _after(self, ctx, req, rsp):
        req["trace"].append(f"after_{self._prefix}")


class ErrorBeforeFilter(BaseFilter):
    """Filter whose _before raises."""

    def __init__(self):
        super().__init__()
        self._name = "error_before"

    async def _before(self, ctx, req, rsp):
        rsp.error = RuntimeError("before_err")
        rsp.is_continue = False


class StreamModifyingFilter(BaseFilter):
    """Filter that appends trace info in stream mode."""

    def __init__(self, tag: str):
        super().__init__()
        self._name = f"stream_{tag}"
        self._tag = tag

    async def _before(self, ctx, req, rsp):
        req["trace"].append(f"before_{self._tag}")

    async def _after(self, ctx, req, rsp):
        req["trace"].append(f"after_{self._tag}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ctx():
    return MagicMock()


# ---------------------------------------------------------------------------
# Tests for stream_handler_adapter
# ---------------------------------------------------------------------------

class TestStreamHandlerAdapter:

    async def test_wraps_non_filter_result(self):
        async def gen():
            yield "raw_value"

        results = []
        async for event in stream_handler_adapter(gen):
            results.append(event)

        assert len(results) == 1
        assert isinstance(results[0], FilterResult)
        assert results[0].rsp == "raw_value"
        assert results[0].is_continue is True

    async def test_passes_filter_result_through(self):
        fr = FilterResult(rsp="already", is_continue=True)

        async def gen():
            yield fr

        results = []
        async for event in stream_handler_adapter(gen):
            results.append(event)

        assert len(results) == 1
        assert results[0] is fr

    async def test_multiple_events(self):
        async def gen():
            yield "a"
            yield FilterResult(rsp="b")
            yield "c"

        results = []
        async for event in stream_handler_adapter(gen):
            results.append(event)

        assert len(results) == 3
        assert results[0].rsp == "a"
        assert results[1].rsp == "b"
        assert results[2].rsp == "c"


# ---------------------------------------------------------------------------
# Tests for coroutine_handler_adapter
# ---------------------------------------------------------------------------

class TestCoroutineHandlerAdapter:

    async def test_returns_filter_result_as_is(self):
        fr = FilterResult(rsp="direct", error=None)

        async def handle():
            return fr

        result = await coroutine_handler_adapter(handle)
        assert result is fr

    async def test_wraps_tuple_result(self):
        async def handle():
            return "val", RuntimeError("e")

        result = await coroutine_handler_adapter(handle)
        assert isinstance(result, FilterResult)
        assert result.rsp == "val"
        assert isinstance(result.error, RuntimeError)

    async def test_wraps_plain_value(self):
        async def handle():
            return "plain"

        result = await coroutine_handler_adapter(handle)
        assert isinstance(result, FilterResult)
        assert result.rsp == "plain"
        assert result.error is None

    async def test_wraps_none(self):
        async def handle():
            return None

        result = await coroutine_handler_adapter(handle)
        assert isinstance(result, FilterResult)
        assert result.rsp is None
        assert result.error is None

    async def test_catches_exception(self):
        async def handle():
            raise ValueError("boom")

        result = await coroutine_handler_adapter(handle)
        assert isinstance(result, FilterResult)
        assert isinstance(result.error, ValueError)
        assert not result.is_continue

    async def test_wraps_tuple_no_error(self):
        async def handle():
            return "ok", None

        result = await coroutine_handler_adapter(handle)
        assert result.rsp == "ok"
        assert result.error is None


# ---------------------------------------------------------------------------
# Tests for run_filters
# ---------------------------------------------------------------------------

class TestRunFilters:

    async def test_no_filters(self, mock_ctx):
        async def handle():
            return "direct_result"

        result = await run_filters(mock_ctx, "req", [], handle)
        assert result == "direct_result"

    async def test_handle_none_raises(self, mock_ctx):
        with pytest.raises(ValueError, match="handle must be provided"):
            await run_filters(mock_ctx, "req", [], None)

    async def test_single_filter_lifecycle(self, mock_ctx):
        f = RecordingFilter("single")

        async def handle():
            return "handle_result"

        result = await run_filters(mock_ctx, "req", [f], handle)
        assert result == "handle_result"
        assert f.before_calls == 1
        assert f.after_calls == 1

    async def test_multiple_filters_execution_order(self, mock_ctx):
        req = {"trace": []}
        f1 = ModifyingBeforeFilter("A")
        f2 = ModifyingBeforeFilter("B")

        async def handle():
            req["trace"].append("handle")
            return "done"

        result = await run_filters(mock_ctx, req, [f1, f2], handle)
        assert result == "done"
        assert req["trace"] == [
            "before_A", "before_B", "handle", "after_B", "after_A"
        ]

    async def test_handle_raises_propagates(self, mock_ctx):
        f = RecordingFilter("r")

        async def handle():
            raise RuntimeError("handle_boom")

        with pytest.raises(RuntimeError, match="handle_boom"):
            await run_filters(mock_ctx, "req", [f], handle)

    async def test_filter_returns_tuple_error(self, mock_ctx):
        async def handle():
            return "val", RuntimeError("tuple_err")

        with pytest.raises(RuntimeError, match="tuple_err"):
            await run_filters(mock_ctx, "req", [], handle)


# ---------------------------------------------------------------------------
# Tests for run_stream_filters
# ---------------------------------------------------------------------------

class TestRunStreamFilters:

    async def test_no_filters(self, mock_ctx):
        async def handle():
            yield "a"
            yield "b"

        results = []
        async for event in run_stream_filters(mock_ctx, "req", [], handle):
            results.append(event)

        assert results == ["a", "b"]

    async def test_handle_none_raises(self, mock_ctx):
        with pytest.raises(ValueError, match="handle must be provided"):
            async for _ in run_stream_filters(mock_ctx, "req", [], None):
                pass

    async def test_single_filter_lifecycle(self, mock_ctx):
        f = RecordingFilter("sf")

        async def handle():
            yield "event1"

        results = []
        async for event in run_stream_filters(mock_ctx, "req", [f], handle):
            results.append(event)

        assert results == ["event1"]
        assert f.before_calls == 1
        assert f.after_calls == 1

    async def test_multiple_filters_execution_order(self, mock_ctx):
        req = {"trace": []}
        f1 = StreamModifyingFilter("A")
        f2 = StreamModifyingFilter("B")

        async def handle():
            req["trace"].append("handle")
            yield "data"

        results = []
        async for event in run_stream_filters(mock_ctx, req, [f1, f2], handle):
            results.append(event)

        assert results == ["data"]
        assert req["trace"] == [
            "before_A", "before_B", "handle", "after_B", "after_A"
        ]

    async def test_yields_rsp_not_filter_result(self, mock_ctx):
        """run_stream_filters should yield event.rsp, not the FilterResult."""
        async def handle():
            yield FilterResult(rsp="wrapped", is_continue=True)

        results = []
        async for event in run_stream_filters(mock_ctx, "req", [], handle):
            results.append(event)

        assert results == ["wrapped"]

    async def test_multiple_stream_events(self, mock_ctx):
        async def handle():
            yield "e1"
            yield "e2"
            yield "e3"

        results = []
        async for event in run_stream_filters(mock_ctx, "req", [], handle):
            results.append(event)

        assert results == ["e1", "e2", "e3"]
