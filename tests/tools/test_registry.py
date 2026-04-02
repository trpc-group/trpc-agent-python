# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._base_tool import BaseTool
from trpc_agent_sdk.tools._function_tool import FunctionTool
from trpc_agent_sdk.tools._registry import (
    ToolRegistry,
    ToolSetRegistry,
    _ToolManager,
    _ToolSetManager,
    get_tool,
    get_tool_set,
    register_tool,
    register_tool_set,
)


class DummyTool(BaseTool):

    def __init__(self, name="dummy", description="dummy tool"):
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        return {}


class TestToolManager:

    def setup_method(self):
        self.manager = _ToolManager()

    def test_add_tool(self):
        tool = DummyTool(name="tool1")
        self.manager.add(tool)
        assert self.manager.get_tool("tool1") is tool

    def test_add_duplicate_raises(self):
        tool = DummyTool(name="tool_dup")
        self.manager.add(tool)
        with pytest.raises(TypeError, match="already exists"):
            self.manager.add(DummyTool(name="tool_dup"))

    def test_get_tool_not_found(self):
        assert self.manager.get_tool("nonexistent") is None

    def test_get_tool_none_returns_all(self):
        t1 = DummyTool(name="a_tool")
        t2 = DummyTool(name="b_tool")
        self.manager.add(t1)
        self.manager.add(t2)
        result = self.manager.get_tool(None)
        assert isinstance(result, list)
        assert len(result) == 2


class TestToolRegistry:

    def test_singleton_behavior(self):
        r1 = ToolRegistry()
        r2 = ToolRegistry()
        assert r1 is r2

    def test_add_and_get(self):
        registry = ToolRegistry()
        tool = DummyTool(name=f"reg_tool_{id(self)}")
        try:
            registry.add(tool)
            assert registry.get(tool.name) is tool
        finally:
            # Cleanup
            registry._tool_registry._instance_map.pop(tool.name, None)

    def test_get_nonexistent(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent_tool_xyz") is None


class TestRegisterToolDecorator:

    def test_register_callable(self):
        unique_name = f"registered_func_{id(self)}"

        def my_func(x: str) -> str:
            """My function."""
            return x

        my_func.__name__ = unique_name

        registry = ToolRegistry()
        try:
            decorator = register_tool()
            result = decorator(my_func)
            assert isinstance(result, FunctionTool)
            found = registry.get(unique_name)
            assert found is not None
        finally:
            registry._tool_registry._instance_map.pop(unique_name, None)

    def test_register_invalid_type_raises(self):
        with pytest.raises(TypeError, match="Can only register"):
            register_tool()(42)


class TestGetTool:

    def test_get_tool_returns_none_for_missing(self):
        result = get_tool("absolutely_nonexistent_tool")
        assert result is None


class TestToolSetManager:

    def setup_method(self):
        self.manager = _ToolSetManager()

    def test_add_toolset(self):
        mock_ts = MagicMock(spec=BaseToolSet)
        mock_ts.name = "ts1"
        self.manager.add(mock_ts)
        assert self.manager.get_tool_set("ts1") is mock_ts

    def test_add_duplicate_raises(self):
        mock_ts = MagicMock(spec=BaseToolSet)
        mock_ts.name = "ts_dup"
        self.manager.add(mock_ts)
        with pytest.raises(TypeError, match="already exists"):
            self.manager.add(mock_ts)

    def test_get_tool_set_none_returns_all(self):
        ts1 = MagicMock(spec=BaseToolSet)
        ts1.name = "ts_a"
        ts2 = MagicMock(spec=BaseToolSet)
        ts2.name = "ts_b"
        self.manager.add(ts1)
        self.manager.add(ts2)
        result = self.manager.get_tool_set(None)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_tool_set_not_found(self):
        assert self.manager.get_tool_set("nonexistent") is None


class TestToolSetRegistry:

    def test_singleton_behavior(self):
        r1 = ToolSetRegistry()
        r2 = ToolSetRegistry()
        assert r1 is r2

    def test_add_and_get(self):
        registry = ToolSetRegistry()
        mock_ts = MagicMock(spec=BaseToolSet)
        mock_ts.name = f"toolset_{id(self)}"
        try:
            registry.add(mock_ts)
            assert registry.get(mock_ts.name) is mock_ts
        finally:
            registry._tool_set_manager._instance_map.pop(mock_ts.name, None)

    def test_get_nonexistent(self):
        registry = ToolSetRegistry()
        assert registry.get("nonexistent_toolset_xyz") is None


class TestGetToolSet:

    def test_returns_none_for_missing(self):
        result = get_tool_set("no_such_toolset")
        assert result is None
