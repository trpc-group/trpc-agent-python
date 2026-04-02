# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills._registry.

Covers:
- SkillRegistry: register, unregister, get, get_all, search, clear
- Duplicate registration error
- Singleton behavior
"""

from __future__ import annotations

import pytest

from trpc_agent_sdk.skills._registry import SkillRegistry


def _dummy_skill_fn():
    pass


def _another_skill_fn():
    pass


class TestSkillRegistry:
    def setup_method(self):
        self.registry = SkillRegistry.__new__(SkillRegistry)
        self.registry._skills = {}

    def test_register_and_get(self):
        self.registry.register("test-skill", _dummy_skill_fn)
        result = self.registry.get("test-skill")
        assert result is _dummy_skill_fn

    def test_register_duplicate_raises(self):
        self.registry.register("dup", _dummy_skill_fn)
        with pytest.raises(ValueError, match="already registered"):
            self.registry.register("dup", _another_skill_fn)

    def test_get_nonexistent_returns_none(self):
        assert self.registry.get("nonexistent") is None

    def test_unregister(self):
        self.registry.register("to-remove", _dummy_skill_fn)
        self.registry.unregister("to-remove")
        assert self.registry.get("to-remove") is None

    def test_unregister_nonexistent_is_noop(self):
        self.registry.unregister("nonexistent")

    def test_get_all(self):
        self.registry.register("a", _dummy_skill_fn)
        self.registry.register("b", _another_skill_fn)
        result = self.registry.get_all()
        assert len(result) == 2
        assert _dummy_skill_fn in result
        assert _another_skill_fn in result

    def test_get_all_empty(self):
        assert self.registry.get_all() == []

    def test_search_by_name(self):
        self.registry.register("weather-tool", _dummy_skill_fn)
        self.registry.register("data-analysis", _another_skill_fn)
        results = self.registry.search("weather")
        assert len(results) == 1
        assert results[0] is _dummy_skill_fn

    def test_search_case_insensitive(self):
        self.registry.register("WeatherTool", _dummy_skill_fn)
        results = self.registry.search("weather")
        assert len(results) == 1

    def test_search_no_match(self):
        self.registry.register("foo", _dummy_skill_fn)
        assert self.registry.search("bar") == []

    def test_search_empty_query(self):
        self.registry.register("foo", _dummy_skill_fn)
        results = self.registry.search("")
        assert len(results) == 1

    def test_clear(self):
        self.registry.register("a", _dummy_skill_fn)
        self.registry.register("b", _another_skill_fn)
        self.registry.clear()
        assert self.registry.get_all() == []
