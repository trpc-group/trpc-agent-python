# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.abc._toolset.

Covers:
- ToolPredicate: runtime_checkable protocol
- ToolSetABC: __init__, initialize, add_tools, _is_tool_selected, close
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import Mock

import pytest

from trpc_agent_sdk.abc._tool import ToolABC
from trpc_agent_sdk.abc._toolset import ToolPredicate, ToolSetABC


class StubTool(ToolABC):
    """Minimal concrete tool for testing."""

    def __init__(self, name: str = "stub_tool"):
        self.name = name

    async def run_async(self, *, tool_context, args):
        return None  # pragma: no cover

    async def process_request(self, *, tool_context, llm_request):
        pass  # pragma: no cover


class StubToolSet(ToolSetABC):
    """Minimal concrete toolset for testing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tools: list[ToolABC] = []

    async def get_tools(self, invocation_context=None):
        return [t for t in self._tools if self._is_tool_selected(t, invocation_context)]


class TestToolPredicate:
    """Tests for the ToolPredicate runtime-checkable protocol."""

    def test_callable_satisfies_protocol(self):
        def my_predicate(tool: ToolABC, invocation_context=None) -> bool:
            return True
        assert isinstance(my_predicate, ToolPredicate)

    def test_lambda_satisfies_protocol(self):
        pred = lambda tool, invocation_context=None: True
        assert isinstance(pred, ToolPredicate)

    def test_non_callable_does_not_satisfy(self):
        assert not isinstance("not_callable", ToolPredicate)
        assert not isinstance(42, ToolPredicate)


class TestToolSetInit:
    """Tests for ToolSetABC initialization."""

    def test_defaults(self):
        ts = StubToolSet()
        assert ts.name == ""
        assert ts._tool_filter is None
        assert ts._is_include_all_tools is True

    def test_custom_name(self):
        ts = StubToolSet(name="my_toolset")
        assert ts.name == "my_toolset"

    def test_tool_filter_list(self):
        ts = StubToolSet(tool_filter=["tool_a", "tool_b"])
        assert ts._tool_filter == ["tool_a", "tool_b"]

    def test_tool_filter_predicate(self):
        pred = lambda tool, ctx=None: True
        ts = StubToolSet(tool_filter=pred)
        assert ts._tool_filter is pred

    def test_is_include_all_tools_false(self):
        ts = StubToolSet(is_include_all_tools=False)
        assert ts._is_include_all_tools is False


class TestInitializeAndAddTools:
    """Tests for initialize() and add_tools() default implementations."""

    def test_initialize_returns_none(self):
        ts = StubToolSet()
        assert ts.initialize() is None

    def test_add_tools_default_is_noop(self):
        ts = StubToolSet()
        ts.add_tools([StubTool()])
        # default add_tools does nothing; _tools stays empty
        assert ts._tools == []


class TestIsToolSelected:
    """Tests for _is_tool_selected logic branches."""

    def test_no_filter_returns_true(self):
        ts = StubToolSet(tool_filter=None)
        tool = StubTool(name="any")
        assert ts._is_tool_selected(tool, None) is True

    def test_include_all_tools_true_returns_true_regardless_of_filter(self):
        ts = StubToolSet(tool_filter=["other_tool"], is_include_all_tools=True)
        tool = StubTool(name="not_in_list")
        assert ts._is_tool_selected(tool, None) is True

    def test_list_filter_includes_matching_tool(self):
        ts = StubToolSet(tool_filter=["tool_a", "tool_b"], is_include_all_tools=False)
        tool = StubTool(name="tool_a")
        assert ts._is_tool_selected(tool, None) is True

    def test_list_filter_excludes_non_matching_tool(self):
        ts = StubToolSet(tool_filter=["tool_a"], is_include_all_tools=False)
        tool = StubTool(name="tool_b")
        assert ts._is_tool_selected(tool, None) is False

    def test_predicate_filter_returns_true(self):
        pred = lambda tool, ctx=None: tool.name.startswith("allow")
        ts = StubToolSet(tool_filter=pred, is_include_all_tools=False)
        assert ts._is_tool_selected(StubTool(name="allow_me"), None) is True

    def test_predicate_filter_returns_false(self):
        pred = lambda tool, ctx=None: tool.name.startswith("allow")
        ts = StubToolSet(tool_filter=pred, is_include_all_tools=False)
        assert ts._is_tool_selected(StubTool(name="deny_me"), None) is False

    def test_predicate_receives_invocation_context(self):
        captured = {}

        def pred(tool, invocation_context=None):
            captured["ctx"] = invocation_context
            return True

        ts = StubToolSet(tool_filter=pred, is_include_all_tools=False)
        sentinel = object()
        ts._is_tool_selected(StubTool(name="t"), sentinel)
        assert captured["ctx"] is sentinel

    def test_empty_list_filter_includes_all(self):
        ts = StubToolSet(tool_filter=[], is_include_all_tools=False)
        tool = StubTool(name="anything")
        assert ts._is_tool_selected(tool, None) is True


class TestClose:
    """Tests for close() default implementation."""

    @pytest.mark.asyncio
    async def test_close_returns_none(self):
        ts = StubToolSet()
        result = await ts.close()
        assert result is None
