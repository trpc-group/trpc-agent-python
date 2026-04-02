# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for FilterRunner."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.abc import FilterResult, FilterType
from trpc_agent_sdk.filter._base_filter import BaseFilter
from trpc_agent_sdk.filter._filter_runner import FilterRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ConcreteRunner(FilterRunner):
    """Concrete subclass for testing (FilterRunner is ABC)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._type = FilterType.AGENT


class StubFilter(BaseFilter):
    """A simple stub filter with a configurable name."""

    def __init__(self, name: str = "stub"):
        super().__init__()
        self._name = name
        self._type = FilterType.AGENT


class PassthroughFilter(BaseFilter):
    """Filter that passes data through untouched."""

    def __init__(self, name: str = "passthrough"):
        super().__init__()
        self._name = name


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ctx():
    return MagicMock()


@pytest.fixture
def runner():
    return ConcreteRunner()


# ---------------------------------------------------------------------------
# Tests for __init__
# ---------------------------------------------------------------------------

class TestFilterRunnerInit:

    def test_defaults(self):
        r = ConcreteRunner()
        assert r.filters_name == []
        assert r.filters == []
        assert r._type == FilterType.AGENT

    def test_with_filters_name(self):
        r = ConcreteRunner(filters_name=["f1", "f2"])
        assert r.filters_name == ["f1", "f2"]

    def test_with_filters(self):
        f = StubFilter("a")
        r = ConcreteRunner(filters=[f])
        assert r.filters == [f]


# ---------------------------------------------------------------------------
# Tests for name property
# ---------------------------------------------------------------------------

class TestFilterRunnerName:

    def test_default_name(self):
        r = ConcreteRunner()
        assert r.name == "ConcreteRunner"

    def test_set_name(self):
        r = ConcreteRunner()
        r.name = "custom"
        assert r.name == "custom"


# ---------------------------------------------------------------------------
# Tests for _init_filters
# ---------------------------------------------------------------------------

class TestInitFilters:

    @patch("trpc_agent_sdk.filter._filter_runner.get_filter")
    def test_success(self, mock_get, runner):
        stub = StubFilter("from_registry")
        mock_get.return_value = stub
        runner._filters_name = ["from_registry"]

        runner._init_filters()

        mock_get.assert_called_once_with(FilterType.AGENT, "from_registry")
        assert runner.filters == [stub]

    @patch("trpc_agent_sdk.filter._filter_runner.get_filter")
    def test_not_found_raises(self, mock_get, runner):
        mock_get.return_value = None
        runner._filters_name = ["missing"]

        with pytest.raises(ValueError, match="Filter missing not found"):
            runner._init_filters()


# ---------------------------------------------------------------------------
# Tests for add_filters
# ---------------------------------------------------------------------------

class TestAddFilters:

    def test_add_instances(self, runner):
        f1, f2 = StubFilter("f1"), StubFilter("f2")
        runner.add_filters([f1, f2])
        assert runner.filters == [f1, f2]

    @patch("trpc_agent_sdk.filter._filter_runner.get_filter")
    def test_add_by_name(self, mock_get, runner):
        stub = StubFilter("reg_filter")
        mock_get.return_value = stub
        runner.add_filters(["reg_filter"])
        assert runner.filters == [stub]

    @patch("trpc_agent_sdk.filter._filter_runner.get_filter")
    def test_add_by_name_not_found(self, mock_get, runner):
        mock_get.return_value = None
        with pytest.raises(ValueError, match="Filter bad not found"):
            runner.add_filters(["bad"])

    def test_add_with_force_replaces(self, runner):
        old = StubFilter("old")
        new = StubFilter("new")
        runner.add_filters([old])
        runner.add_filters([new], force=True)
        assert runner.filters == [new]

    def test_add_without_force_extends(self, runner):
        f1, f2 = StubFilter("f1"), StubFilter("f2")
        runner.add_filters([f1])
        runner.add_filters([f2])
        assert runner.filters == [f1, f2]

    def test_add_none_filters(self, runner):
        runner.add_filters(None)
        assert runner.filters == []

    def test_add_mixed_types(self, runner):
        f1 = StubFilter("inst")
        with patch("trpc_agent_sdk.filter._filter_runner.get_filter") as mock_get:
            f2 = StubFilter("from_reg")
            mock_get.return_value = f2
            runner.add_filters([f1, "from_reg"])
        assert runner.filters == [f1, f2]


# ---------------------------------------------------------------------------
# Tests for add_one_filter
# ---------------------------------------------------------------------------

class TestAddOneFilter:

    def test_add_instance(self, runner):
        f = StubFilter("one")
        runner.add_one_filter(f)
        assert runner.filters == [f]

    @patch("trpc_agent_sdk.filter._filter_runner.get_filter")
    def test_add_by_name(self, mock_get, runner):
        stub = StubFilter("reg")
        mock_get.return_value = stub
        runner.add_one_filter("reg")
        assert runner.filters == [stub]

    @patch("trpc_agent_sdk.filter._filter_runner.get_filter")
    def test_add_by_name_not_found(self, mock_get, runner):
        mock_get.return_value = None
        with pytest.raises(ValueError, match="Filter missing not found"):
            runner.add_one_filter("missing")

    def test_no_duplicate_without_force(self, runner):
        f = StubFilter("dup")
        runner.add_one_filter(f)
        runner.add_one_filter(f)
        assert len(runner.filters) == 1

    def test_duplicate_with_force(self, runner):
        f1 = StubFilter("same_name")
        f2 = StubFilter("same_name")
        runner.add_one_filter(f1)
        runner.add_one_filter(f2, force=True)
        assert len(runner.filters) == 2

    def test_add_at_index(self, runner):
        f1, f2, f3 = StubFilter("a"), StubFilter("b"), StubFilter("c")
        runner.add_one_filter(f1)
        runner.add_one_filter(f3)
        runner.add_one_filter(f2, index=1, force=True)
        assert [f.name for f in runner.filters] == ["a", "b", "c"]

    def test_add_at_index_none_appends(self, runner):
        f1, f2 = StubFilter("x"), StubFilter("y")
        runner.add_one_filter(f1)
        runner.add_one_filter(f2)
        assert runner.filters == [f1, f2]


# ---------------------------------------------------------------------------
# Tests for get_filter
# ---------------------------------------------------------------------------

class TestGetFilter:

    def test_found(self, runner):
        f = StubFilter("target")
        runner._filters = [StubFilter("other"), f]
        assert runner.get_filter("target") is f

    def test_not_found_raises(self, runner):
        with pytest.raises(ValueError, match="Filter nope not found"):
            runner.get_filter("nope")


# ---------------------------------------------------------------------------
# Tests for _run_filters / _run_stream_filters
# ---------------------------------------------------------------------------

class TestRunFiltersMethods:

    async def test_run_filters_delegates(self, mock_ctx):
        runner = ConcreteRunner()
        f = PassthroughFilter("p1")
        runner._filters = [f]

        async def handle():
            return "final"

        with patch("trpc_agent_sdk.filter._filter_runner.run_filters", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "result"
            result = await runner._run_filters(mock_ctx, "req", handle)
            assert result == "result"
            call_args = mock_run.call_args
            assert call_args[0][0] is mock_ctx
            assert call_args[0][1] == "req"
            assert len(call_args[0][2]) == 1
            assert call_args[0][3] is handle

    async def test_run_filters_with_extra(self, mock_ctx):
        runner = ConcreteRunner()
        f1 = PassthroughFilter("p1")
        f2 = PassthroughFilter("p2")
        runner._filters = [f1]

        async def handle():
            return "final"

        with patch("trpc_agent_sdk.filter._filter_runner.run_filters", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "result"
            await runner._run_filters(mock_ctx, "req", handle, extra_filters=[f2])
            filters_passed = mock_run.call_args[0][2]
            assert len(filters_passed) == 2

    async def test_run_stream_filters_delegates(self, mock_ctx):
        runner = ConcreteRunner()
        f = PassthroughFilter("p1")
        runner._filters = [f]

        async def handle():
            yield "data"

        with patch("trpc_agent_sdk.filter._filter_runner.run_stream_filters") as mock_run:
            async def mock_gen(*args, **kwargs):
                yield "streamed"

            mock_run.return_value = mock_gen()

            events = []
            async for event in runner._run_stream_filters(mock_ctx, "req", handle):
                events.append(event)

            assert events == ["streamed"]

    async def test_run_filters_does_not_mutate_internal_list(self, mock_ctx):
        runner = ConcreteRunner()
        f1 = PassthroughFilter("p1")
        extra = PassthroughFilter("extra")
        runner._filters = [f1]

        async def handle():
            return "final"

        with patch("trpc_agent_sdk.filter._filter_runner.run_filters", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "result"
            await runner._run_filters(mock_ctx, "req", handle, extra_filters=[extra])

        assert len(runner._filters) == 1
