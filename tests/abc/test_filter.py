# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for the filter abstractions in trpc_agent_sdk.abc._filter.

Covers:
- FilterResult: dataclass fields, __iter__, defaults
- FilterType: enum values and uniqueness
- FilterABC: concrete properties (full_name, type, name) and _create_err_str
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest

from trpc_agent_sdk.abc._filter import (
    FilterABC,
    FilterAsyncGenHandleType,
    FilterAsyncGenReturnType,
    FilterHandleType,
    FilterResult,
    FilterType,
)


class StubFilter(FilterABC):
    """Minimal concrete filter for testing FilterABC base logic."""

    async def _before(self, ctx, req, rsp):
        return None

    async def _after(self, ctx, req, rsp):
        return None

    async def _after_every_stream(self, ctx, req, rsp):
        return None

    async def run_stream(self, ctx, req, handle):
        async for item in handle():
            yield item

    async def run(self, ctx, req, handle):
        return FilterResult()


class TestFilterResult:
    """Tests for the FilterResult dataclass."""

    def test_defaults(self):
        result = FilterResult()
        assert result.rsp is None
        assert result.error is None
        assert result.is_continue is True

    def test_custom_values(self):
        err = ValueError("boom")
        result = FilterResult(rsp="data", error=err, is_continue=False)
        assert result.rsp == "data"
        assert result.error is err
        assert result.is_continue is False

    def test_iter_returns_rsp_and_error(self):
        err = RuntimeError("x")
        result = FilterResult(rsp=42, error=err)
        rsp, error = result
        assert rsp == 42
        assert error is err

    def test_iter_with_defaults(self):
        rsp, error = FilterResult()
        assert rsp is None
        assert error is None

    def test_iter_unpacks_to_list(self):
        result = FilterResult(rsp="a", error=None, is_continue=True)
        items = list(result)
        assert items == ["a", None]

    def test_generic_type_parameterization(self):
        result = FilterResult[int](rsp=123)
        assert result.rsp == 123


class TestFilterType:
    """Tests for the FilterType enum."""

    def test_unsupported_value(self):
        assert FilterType.UNSUPPORTED == -1

    def test_tool_value(self):
        assert FilterType.TOOL == 0

    def test_model_value(self):
        assert FilterType.MODEL == 1

    def test_agent_value(self):
        assert FilterType.AGENT == 2

    def test_all_values_are_unique(self):
        values = [m.value for m in FilterType]
        assert len(values) == len(set(values))

    def test_names(self):
        assert FilterType.UNSUPPORTED.name == "UNSUPPORTED"
        assert FilterType.TOOL.name == "TOOL"
        assert FilterType.MODEL.name == "MODEL"
        assert FilterType.AGENT.name == "AGENT"

    def test_int_comparison(self):
        assert FilterType.TOOL < FilterType.MODEL < FilterType.AGENT


class TestFilterABC:
    """Tests for FilterABC concrete properties and helper methods."""

    def test_default_type_is_unsupported(self):
        f = StubFilter()
        assert f.type == FilterType.UNSUPPORTED

    def test_default_name_is_empty(self):
        f = StubFilter()
        assert f.name == ""

    def test_set_type(self):
        f = StubFilter()
        f.type = FilterType.MODEL
        assert f.type == FilterType.MODEL

    def test_set_name(self):
        f = StubFilter()
        f.name = "my_filter"
        assert f.name == "my_filter"

    def test_full_name_combines_type_and_name(self):
        f = StubFilter()
        f.type = FilterType.AGENT
        f.name = "auth"
        assert f.full_name == "AGENT_auth"

    def test_full_name_with_defaults(self):
        f = StubFilter()
        assert f.full_name == "UNSUPPORTED_"

    def test_create_err_str_includes_type_and_name(self):
        f = StubFilter()
        f.type = FilterType.TOOL
        f.name = "calculator"
        err = f._create_err_str("something went wrong")
        assert "TOOL" in err
        assert "calculator" in err
        assert "something went wrong" in err

    def test_create_err_str_format(self):
        f = StubFilter()
        f.type = FilterType.MODEL
        f.name = "gpt"
        expected = "filter type: 'MODEL', name: 'gpt': (timeout)"
        assert f._create_err_str("timeout") == expected
