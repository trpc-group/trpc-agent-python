# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for trpc_agent_sdk.types._state.

Covers:
    - State: __getitem__, __setitem__, __contains__,
      has_delta, get, update, to_dict, class prefixes
"""

from __future__ import annotations

import pytest

from trpc_agent_sdk.types._state import State


class TestStatePrefixes:
    """Class-level prefix constants."""

    def test_app_prefix(self):
        assert State.APP_PREFIX == "app:"

    def test_user_prefix(self):
        assert State.USER_PREFIX == "user:"

    def test_temp_prefix(self):
        assert State.TEMP_PREFIX == "temp:"


class TestStateInit:
    """Construction and initial state."""

    def test_empty(self):
        s = State(value={}, delta={})
        assert s.to_dict() == {}
        assert not s.has_delta()

    def test_with_value_only(self):
        s = State(value={"a": 1}, delta={})
        assert s["a"] == 1
        assert not s.has_delta()

    def test_with_delta_only(self):
        s = State(value={}, delta={"b": 2})
        assert s["b"] == 2
        assert s.has_delta()

    def test_with_both(self):
        s = State(value={"a": 1}, delta={"b": 2})
        assert s["a"] == 1
        assert s["b"] == 2


class TestStateGetItem:
    """__getitem__ behaviour — delta takes precedence over value."""

    def test_from_value(self):
        s = State(value={"k": "v"}, delta={})
        assert s["k"] == "v"

    def test_from_delta(self):
        s = State(value={}, delta={"k": "d"})
        assert s["k"] == "d"

    def test_delta_overrides_value(self):
        s = State(value={"k": "old"}, delta={"k": "new"})
        assert s["k"] == "new"

    def test_missing_key_raises(self):
        s = State(value={}, delta={})
        with pytest.raises(KeyError):
            _ = s["missing"]


class TestStateSetItem:
    """__setitem__ writes to both value and delta."""

    def test_set_new_key(self):
        s = State(value={}, delta={})
        s["x"] = 42
        assert s["x"] == 42
        assert s.has_delta()

    def test_set_overwrites_existing(self):
        s = State(value={"x": 1}, delta={})
        s["x"] = 99
        assert s["x"] == 99

    def test_set_updates_delta(self):
        s = State(value={}, delta={})
        s["k"] = "v"
        assert s._delta["k"] == "v"

    def test_set_updates_value(self):
        s = State(value={}, delta={})
        s["k"] = "v"
        assert s._value["k"] == "v"


class TestStateContains:
    """__contains__ checks both value and delta."""

    def test_in_value(self):
        s = State(value={"a": 1}, delta={})
        assert "a" in s

    def test_in_delta(self):
        s = State(value={}, delta={"b": 2})
        assert "b" in s

    def test_not_present(self):
        s = State(value={}, delta={})
        assert "z" not in s

    def test_in_both(self):
        s = State(value={"c": 1}, delta={"c": 2})
        assert "c" in s


class TestStateHasDelta:
    """has_delta() reflects pending changes."""

    def test_no_delta(self):
        s = State(value={"a": 1}, delta={})
        assert not s.has_delta()

    def test_with_delta(self):
        s = State(value={}, delta={"a": 1})
        assert s.has_delta()

    def test_after_set(self):
        s = State(value={}, delta={})
        s["key"] = "value"
        assert s.has_delta()


class TestStateGet:
    """get() with default fallback."""

    def test_existing_key(self):
        s = State(value={"k": 10}, delta={})
        assert s.get("k") == 10

    def test_missing_key_default_none(self):
        s = State(value={}, delta={})
        assert s.get("missing") is None

    def test_missing_key_custom_default(self):
        s = State(value={}, delta={})
        assert s.get("missing", 42) == 42

    def test_delta_key(self):
        s = State(value={}, delta={"d": "yes"})
        assert s.get("d") == "yes"

    def test_delta_overrides_in_get(self):
        s = State(value={"k": "old"}, delta={"k": "new"})
        assert s.get("k") == "new"


class TestStateUpdate:
    """update() merges into both value and delta."""

    def test_update_empty(self):
        s = State(value={"a": 1}, delta={})
        s.update({})
        assert s.to_dict() == {"a": 1}
        assert not s.has_delta()

    def test_update_adds_keys(self):
        s = State(value={}, delta={})
        s.update({"x": 1, "y": 2})
        assert s["x"] == 1
        assert s["y"] == 2
        assert s.has_delta()

    def test_update_overwrites(self):
        s = State(value={"x": 0}, delta={})
        s.update({"x": 99})
        assert s["x"] == 99


class TestStateToDict:
    """to_dict() merges value and delta."""

    def test_empty(self):
        s = State(value={}, delta={})
        assert s.to_dict() == {}

    def test_value_only(self):
        s = State(value={"a": 1, "b": 2}, delta={})
        assert s.to_dict() == {"a": 1, "b": 2}

    def test_delta_only(self):
        s = State(value={}, delta={"c": 3})
        assert s.to_dict() == {"c": 3}

    def test_merged(self):
        s = State(value={"a": 1}, delta={"b": 2})
        assert s.to_dict() == {"a": 1, "b": 2}

    def test_delta_overrides_value_in_dict(self):
        s = State(value={"k": "old"}, delta={"k": "new"})
        assert s.to_dict()["k"] == "new"

    def test_to_dict_returns_copy(self):
        s = State(value={"a": 1}, delta={})
        d = s.to_dict()
        d["a"] = 999
        assert s["a"] == 1
