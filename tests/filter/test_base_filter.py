# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for BaseFilter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.abc import FilterResult, FilterType
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.filter._base_filter import BaseFilter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class NoopFilter(BaseFilter):
    """A concrete filter with no-op hooks for baseline tests."""
    pass


class BeforeYieldsFilter(BaseFilter):
    """Filter whose _before is an async generator yielding FilterResult."""

    async def _before(self, ctx, req, rsp):
        yield FilterResult(rsp="before_value", is_continue=True)


class BeforeReturnsRspFilter(BaseFilter):
    """Filter whose _before returns a plain rsp value (coroutine path)."""

    async def _before(self, ctx, req, rsp):
        rsp.rsp = "before_rsp"
        return None


class BeforeReturnsTupleFilter(BaseFilter):
    """Filter whose _before returns a (rsp, error) tuple via coroutine path."""

    def __init__(self, rsp_val=None, err_val=None):
        super().__init__()
        self._rsp_val = rsp_val
        self._err_val = err_val

    async def _before(self, ctx, req, rsp):
        return self._rsp_val, self._err_val


class BeforeRaisesFilter(BaseFilter):
    """Filter whose _before raises an arbitrary exception."""

    async def _before(self, ctx, req, rsp):
        raise RuntimeError("before boom")


class BeforeRaisesRunCancelledFilter(BaseFilter):
    """Filter whose _before raises RunCancelledException."""

    async def _before(self, ctx, req, rsp):
        raise RunCancelledException("cancelled")


class AfterRaisesFilter(BaseFilter):
    """Filter whose _after raises an arbitrary exception."""

    async def _after(self, ctx, req, rsp):
        raise RuntimeError("after boom")


class AfterYieldsNotContinueFilter(BaseFilter):
    """Filter whose _after yields a result with is_continue=False."""

    async def _after(self, ctx, req, rsp):
        yield FilterResult(rsp="stopped", is_continue=False)


class BeforeYieldsErrorFilter(BaseFilter):
    """Filter whose _before yields a FilterResult with an error."""

    async def _before(self, ctx, req, rsp):
        yield FilterResult(rsp=None, error=RuntimeError("err"), is_continue=False)


class BeforeYieldsWrongTypeFilter(BaseFilter):
    """Filter whose _before async-gen yields a non-FilterResult value."""

    async def _before(self, ctx, req, rsp):
        yield "not_a_filter_result"


class AfterEveryStreamRecorder(BaseFilter):
    """Filter that records every stream event via _after_every_stream."""

    def __init__(self):
        super().__init__()
        self.stream_events: list[FilterResult] = []

    async def _after_every_stream(self, ctx, req, rsp):
        self.stream_events.append(rsp)


class BeforeStopsContinueFilter(BaseFilter):
    """Filter whose _before sets is_continue=False on rsp (coroutine path)."""

    async def _before(self, ctx, req, rsp):
        rsp.rsp = "stopped_before"
        rsp.is_continue = False
        rsp.error = RuntimeError("stopped")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ctx():
    return MagicMock()


@pytest.fixture
def mock_req():
    return {"key": "value"}


# ---------------------------------------------------------------------------
# Tests for default no-op hooks
# ---------------------------------------------------------------------------

class TestBaseFilterDefaults:

    async def test_before_returns_none(self, mock_ctx, mock_req):
        f = NoopFilter()
        result = await f._before(mock_ctx, mock_req, FilterResult())
        assert result is None

    async def test_after_returns_none(self, mock_ctx, mock_req):
        f = NoopFilter()
        result = await f._after(mock_ctx, mock_req, FilterResult())
        assert result is None

    async def test_after_every_stream_returns_none(self, mock_ctx, mock_req):
        f = NoopFilter()
        result = await f._after_every_stream(mock_ctx, mock_req, FilterResult())
        assert result is None


# ---------------------------------------------------------------------------
# Tests for _handle_co — async generator branch
# ---------------------------------------------------------------------------

class TestHandleCoAsyncGen:

    async def test_yields_filter_results(self, mock_ctx):
        f = NoopFilter()

        async def gen():
            yield FilterResult(rsp="a", is_continue=True)
            yield FilterResult(rsp="b", is_continue=True)

        results = []
        async for event in f._handle_co(FilterResult(), gen(), "test"):
            results.append(event)

        assert len(results) == 2
        assert results[0].rsp == "a"
        assert results[1].rsp == "b"

    async def test_stops_on_is_continue_false(self, mock_ctx):
        f = NoopFilter()

        async def gen():
            yield FilterResult(rsp="first", is_continue=False)
            yield FilterResult(rsp="should_not_reach")

        results = []
        async for event in f._handle_co(FilterResult(), gen(), "test"):
            results.append(event)

        assert len(results) == 1
        assert results[0].rsp == "first"

    async def test_logs_error_in_result(self, mock_ctx):
        f = NoopFilter()

        async def gen():
            yield FilterResult(rsp=None, error=RuntimeError("oops"), is_continue=True)

        results = []
        async for event in f._handle_co(FilterResult(), gen(), "test"):
            results.append(event)

        assert len(results) == 1
        assert isinstance(results[0].error, RuntimeError)

    async def test_non_filter_result_yields_error(self, mock_ctx):
        """Non-FilterResult from async gen triggers TypeError caught as error result."""
        f = NoopFilter()

        async def gen():
            yield "bad"

        events = []
        async for event in f._handle_co(FilterResult(), gen(), "test"):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0].error, TypeError)
        assert not events[0].is_continue

    async def test_calls_handle_event_callback(self, mock_ctx):
        f = NoopFilter()
        callback = AsyncMock()

        async def gen():
            yield FilterResult(rsp="x", is_continue=True)

        async for _ in f._handle_co(FilterResult(), gen(), "test", handle_event=callback):
            pass

        callback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests for _handle_co — coroutine branch
# ---------------------------------------------------------------------------

class TestHandleCoCoro:

    async def test_coroutine_returns_plain_value(self, mock_ctx):
        f = NoopFilter()

        async def coro():
            return "plain"

        result = FilterResult()
        events = []
        async for event in f._handle_co(result, coro(), "test"):
            events.append(event)

        assert len(events) == 1
        assert events[0].rsp == "plain"

    async def test_coroutine_returns_tuple(self, mock_ctx):
        f = NoopFilter()

        async def coro():
            return "val", RuntimeError("err")

        result = FilterResult()
        events = []
        async for event in f._handle_co(result, coro(), "test"):
            events.append(event)

        assert len(events) == 1
        assert events[0].rsp == "val"
        assert isinstance(events[0].error, RuntimeError)
        assert not events[0].is_continue

    async def test_coroutine_returns_none(self, mock_ctx):
        f = NoopFilter()

        async def coro():
            return None

        result = FilterResult()
        events = []
        async for event in f._handle_co(result, coro(), "test"):
            events.append(event)

        assert len(events) == 0

    async def test_coroutine_returns_tuple_no_error(self, mock_ctx):
        f = NoopFilter()

        async def coro():
            return "ok_val", None

        result = FilterResult()
        events = []
        async for event in f._handle_co(result, coro(), "test"):
            events.append(event)

        assert len(events) == 1
        assert events[0].rsp == "ok_val"
        assert events[0].error is None
        assert events[0].is_continue is True


# ---------------------------------------------------------------------------
# Tests for _handle_co — exception handling
# ---------------------------------------------------------------------------

class TestHandleCoExceptions:

    async def test_run_cancelled_re_raised(self, mock_ctx):
        f = NoopFilter()

        async def coro():
            raise RunCancelledException("stop")

        with pytest.raises(RunCancelledException):
            async for _ in f._handle_co(FilterResult(), coro(), "test"):
                pass

    async def test_generic_exception_yields_error_result(self, mock_ctx):
        f = NoopFilter()

        async def coro():
            raise ValueError("oops")

        events = []
        async for event in f._handle_co(FilterResult(), coro(), "test"):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0].error, ValueError)
        assert not events[0].is_continue

    async def test_async_gen_run_cancelled_re_raised(self, mock_ctx):
        f = NoopFilter()

        async def gen():
            raise RunCancelledException("stop")
            yield  # noqa: unreachable

        with pytest.raises(RunCancelledException):
            async for _ in f._handle_co(FilterResult(), gen(), "test"):
                pass

    async def test_async_gen_generic_exception_yields_error(self, mock_ctx):
        f = NoopFilter()

        async def gen():
            raise ValueError("gen oops")
            yield  # noqa: unreachable

        events = []
        async for event in f._handle_co(FilterResult(), gen(), "test"):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0].error, ValueError)


# ---------------------------------------------------------------------------
# Tests for run_stream
# ---------------------------------------------------------------------------

class TestRunStream:

    async def test_full_lifecycle(self, mock_ctx, mock_req):
        """before -> handle -> after all execute in order."""
        f = NoopFilter()
        call_order = []

        original_before = f._before
        original_after = f._after

        async def tracked_before(ctx, req, rsp):
            call_order.append("before")
            return await original_before(ctx, req, rsp)

        async def tracked_after(ctx, req, rsp):
            call_order.append("after")
            return await original_after(ctx, req, rsp)

        f._before = tracked_before
        f._after = tracked_after

        async def handle():
            call_order.append("handle")
            yield FilterResult(rsp="handle_data", is_continue=True)

        events = []
        async for event in f.run_stream(mock_ctx, mock_req, handle):
            events.append(event)

        assert call_order == ["before", "handle", "after"]
        assert len(events) == 1
        assert events[0].rsp == "handle_data"

    async def test_before_stops_lifecycle(self, mock_ctx, mock_req):
        f = BeforeYieldsErrorFilter()

        async def handle():
            yield FilterResult(rsp="should_not_reach")

        events = []
        async for event in f.run_stream(mock_ctx, mock_req, handle):
            events.append(event)

        assert len(events) == 1
        assert not events[0].is_continue

    async def test_handle_stops_lifecycle(self, mock_ctx, mock_req):
        f = NoopFilter()

        async def handle():
            yield FilterResult(rsp="fail", is_continue=False)

        events = []
        async for event in f.run_stream(mock_ctx, mock_req, handle):
            events.append(event)

        assert len(events) == 1
        assert not events[0].is_continue

    async def test_after_every_stream_called_for_each_event(self, mock_ctx, mock_req):
        f = AfterEveryStreamRecorder()

        async def handle():
            yield FilterResult(rsp="e1", is_continue=True)
            yield FilterResult(rsp="e2", is_continue=True)

        events = []
        async for event in f.run_stream(mock_ctx, mock_req, handle):
            events.append(event)

        assert len(f.stream_events) == 2

    async def test_before_raises_exception(self, mock_ctx, mock_req):
        f = BeforeRaisesFilter()

        async def handle():
            yield FilterResult(rsp="never")

        events = []
        async for event in f.run_stream(mock_ctx, mock_req, handle):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0].error, RuntimeError)
        assert not events[0].is_continue


# ---------------------------------------------------------------------------
# Tests for run (non-stream)
# ---------------------------------------------------------------------------

class TestRun:

    async def test_full_lifecycle(self, mock_ctx, mock_req):
        f = NoopFilter()

        async def handle():
            return FilterResult(rsp="handle_result", is_continue=True)

        result = await f.run(mock_ctx, mock_req, handle)
        assert isinstance(result, FilterResult)
        assert result.rsp == "handle_result"

    async def test_before_error_stops(self, mock_ctx, mock_req):
        f = BeforeStopsContinueFilter()

        async def handle():
            return FilterResult(rsp="never")

        result = await f.run(mock_ctx, mock_req, handle)
        assert isinstance(result, FilterResult)
        assert result.rsp == "stopped_before"
        assert not result.is_continue

    async def test_before_exception_returns_error_tuple(self, mock_ctx, mock_req):
        f = BeforeRaisesFilter()

        async def handle():
            return FilterResult(rsp="never")

        result = await f.run(mock_ctx, mock_req, handle)
        rsp, error = result
        assert rsp is None
        assert isinstance(error, RuntimeError)

    async def test_handle_returns_tuple(self, mock_ctx, mock_req):
        f = NoopFilter()

        async def handle():
            return "val", RuntimeError("handle_err")

        result = await f.run(mock_ctx, mock_req, handle)
        assert isinstance(result, FilterResult)
        assert result.rsp == "val"
        assert isinstance(result.error, RuntimeError)
        assert not result.is_continue

    async def test_handle_returns_plain_value(self, mock_ctx, mock_req):
        f = NoopFilter()

        async def handle():
            return "plain_value"

        result = await f.run(mock_ctx, mock_req, handle)
        assert isinstance(result, FilterResult)
        assert result.rsp == "plain_value"

    async def test_handle_returns_filter_result(self, mock_ctx, mock_req):
        f = NoopFilter()

        async def handle():
            return FilterResult(rsp="fr_val", is_continue=True)

        result = await f.run(mock_ctx, mock_req, handle)
        assert result.rsp == "fr_val"

    async def test_handle_error_stops_before_after(self, mock_ctx, mock_req):
        f = NoopFilter()
        after_called = False

        original_after = f._after

        async def tracked_after(ctx, req, rsp):
            nonlocal after_called
            after_called = True
            return await original_after(ctx, req, rsp)

        f._after = tracked_after

        async def handle():
            return FilterResult(rsp="x", error=RuntimeError("handle_err"), is_continue=False)

        result = await f.run(mock_ctx, mock_req, handle)
        assert not after_called
        assert isinstance(result.error, RuntimeError)

    async def test_after_exception_sets_error(self, mock_ctx, mock_req):
        f = AfterRaisesFilter()

        async def handle():
            return FilterResult(rsp="ok", is_continue=True)

        result = await f.run(mock_ctx, mock_req, handle)
        assert isinstance(result.error, RuntimeError)
        assert not result.is_continue


# ---------------------------------------------------------------------------
# Tests for FilterResult dataclass
# ---------------------------------------------------------------------------

class TestFilterResult:

    def test_defaults(self):
        r = FilterResult()
        assert r.rsp is None
        assert r.error is None
        assert r.is_continue is True

    def test_iter_unpacking(self):
        r = FilterResult(rsp="val", error=RuntimeError("e"))
        rsp, error = r
        assert rsp == "val"
        assert isinstance(error, RuntimeError)

    def test_custom_values(self):
        r = FilterResult(rsp=42, error=None, is_continue=False)
        assert r.rsp == 42
        assert not r.is_continue
