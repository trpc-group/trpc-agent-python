# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for FilterRegistry and related functions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.filter._base_filter import BaseFilter
from trpc_agent_sdk.filter._registry import (
    FilterRegistry,
    _FilterManager,
    get_agent_filter,
    get_filter,
    get_model_filter,
    get_tool_filter,
    register_agent_filter,
    register_filter,
    register_model_filter,
    register_tool_filter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SampleFilter(BaseFilter):
    """A concrete filter for registration tests."""
    pass


class AnotherFilter(BaseFilter):
    """Another concrete filter."""
    pass


# ---------------------------------------------------------------------------
# Tests for _FilterManager
# ---------------------------------------------------------------------------

class TestFilterManager:

    def test_init_sets_type(self):
        mgr = _FilterManager(FilterType.MODEL)
        assert mgr.filter_type == FilterType.MODEL

    def test_default_type(self):
        mgr = _FilterManager()
        assert mgr.filter_type == FilterType.UNSUPPORTED

    def test_register_filter_decorator(self):
        mgr = _FilterManager(FilterType.TOOL)
        decorator = mgr.register_filter("my_filter")

        @decorator
        class TestFilter(BaseFilter):
            pass

        instance = mgr.get_instance("my_filter")
        assert instance is not None
        assert isinstance(instance, BaseFilter)
        assert instance.name == "my_filter"
        assert instance.type == FilterType.TOOL

    def test_register_filter_sets_name_and_type(self):
        mgr = _FilterManager(FilterType.AGENT)

        @mgr.register_filter("named")
        class NamedFilter(BaseFilter):
            pass

        inst = mgr.get_instance("named")
        assert inst.name == "named"
        assert inst.type == FilterType.AGENT

    def test_register_duplicate_class_raises(self):
        mgr = _FilterManager(FilterType.TOOL)

        @mgr.register_filter("first")
        class DupFilter(BaseFilter):
            pass

        with pytest.raises(TypeError, match="already registered"):
            mgr.register("DupFilter", DupFilter)


# ---------------------------------------------------------------------------
# Tests for FilterRegistry
# ---------------------------------------------------------------------------

class TestFilterRegistry:

    def _fresh_registry(self):
        """Create a fresh FilterRegistry by clearing singleton state."""
        from trpc_agent_sdk.utils._singleton import SingletonMeta
        if FilterRegistry in SingletonMeta._instances:
            del SingletonMeta._instances[FilterRegistry]
        return FilterRegistry()

    def test_singleton(self):
        r1 = FilterRegistry()
        r2 = FilterRegistry()
        assert r1 is r2

    def test_register_and_get(self):
        registry = self._fresh_registry()

        @registry.register(FilterType.MODEL, "test_model_filter")
        class TestModelFilter(BaseFilter):
            pass

        inst = registry.get(FilterType.MODEL, "test_model_filter")
        assert inst is not None
        assert isinstance(inst, BaseFilter)
        assert inst.name == "test_model_filter"

    def test_get_nonexistent_returns_none(self):
        registry = self._fresh_registry()
        result = registry.get(FilterType.TOOL, "nonexistent_filter_xyz")
        assert result is None

    def test_registers_all_types(self):
        registry = self._fresh_registry()
        for ft in FilterType:
            if ft != FilterType.UNSUPPORTED:
                assert ft in registry._filter_registry


# ---------------------------------------------------------------------------
# Tests for module-level functions
# ---------------------------------------------------------------------------

class TestModuleFunctions:

    def test_register_filter_calls_registry(self):
        with patch.object(FilterRegistry, "register") as mock_reg:
            mock_reg.return_value = lambda cls: cls
            register_filter(FilterType.TOOL, "deco_filter")
            mock_reg.assert_called_once_with(FilterType.TOOL, "deco_filter")

    def test_get_filter_calls_registry(self):
        with patch.object(FilterRegistry, "get") as mock_get:
            mock_get.return_value = None
            result = get_filter(FilterType.MODEL, "some_filter")
            mock_get.assert_called_once_with(FilterType.MODEL, "some_filter")
            assert result is None


# ---------------------------------------------------------------------------
# Tests for partial helpers
# ---------------------------------------------------------------------------

class TestPartialHelpers:

    def test_register_tool_filter(self):
        with patch("trpc_agent_sdk.filter._registry.FilterRegistry") as MockReg:
            mock_instance = MockReg.return_value
            mock_instance.register.return_value = lambda cls: cls
            register_tool_filter("tf")
            mock_instance.register.assert_called_with(FilterType.TOOL, "tf")

    def test_register_model_filter(self):
        with patch("trpc_agent_sdk.filter._registry.FilterRegistry") as MockReg:
            mock_instance = MockReg.return_value
            mock_instance.register.return_value = lambda cls: cls
            register_model_filter("mf")
            mock_instance.register.assert_called_with(FilterType.MODEL, "mf")

    def test_register_agent_filter(self):
        with patch("trpc_agent_sdk.filter._registry.FilterRegistry") as MockReg:
            mock_instance = MockReg.return_value
            mock_instance.register.return_value = lambda cls: cls
            register_agent_filter("af")
            mock_instance.register.assert_called_with(FilterType.AGENT, "af")

    def test_get_tool_filter(self):
        with patch("trpc_agent_sdk.filter._registry.FilterRegistry") as MockReg:
            mock_instance = MockReg.return_value
            mock_instance.get.return_value = None
            get_tool_filter("tf")
            mock_instance.get.assert_called_with(FilterType.TOOL, "tf")

    def test_get_model_filter(self):
        with patch("trpc_agent_sdk.filter._registry.FilterRegistry") as MockReg:
            mock_instance = MockReg.return_value
            mock_instance.get.return_value = None
            get_model_filter("mf")
            mock_instance.get.assert_called_with(FilterType.MODEL, "mf")

    def test_get_agent_filter(self):
        with patch("trpc_agent_sdk.filter._registry.FilterRegistry") as MockReg:
            mock_instance = MockReg.return_value
            mock_instance.get.return_value = None
            get_agent_filter("af")
            mock_instance.get.assert_called_with(FilterType.AGENT, "af")
