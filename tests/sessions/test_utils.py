# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.sessions._utils.

Covers:
- StateStorageEntry dataclass
- extract_state_delta: app/user/session/temp prefix handling
- merge_state: merge with/without copy
- session_key, app_state_key, user_state_key formatting
"""

from __future__ import annotations

import copy

import pytest

from trpc_agent_sdk.sessions._utils import (
    StateStorageEntry,
    app_state_key,
    extract_state_delta,
    merge_state,
    session_key,
    user_state_key,
)
from trpc_agent_sdk.types import State


class TestStateStorageEntry:
    """Test StateStorageEntry dataclass."""

    def test_defaults(self):
        entry = StateStorageEntry()
        assert entry.app_state_delta == {}
        assert entry.user_state_delta == {}
        assert entry.session_state == {}

    def test_custom_values(self):
        entry = StateStorageEntry(
            app_state_delta={"a": 1},
            user_state_delta={"b": 2},
            session_state={"c": 3},
        )
        assert entry.app_state_delta == {"a": 1}
        assert entry.user_state_delta == {"b": 2}
        assert entry.session_state == {"c": 3}


class TestExtractStateDelta:
    """Test extract_state_delta function."""

    def test_none_input(self):
        result = extract_state_delta(None)
        assert result.app_state_delta == {}
        assert result.user_state_delta == {}
        assert result.session_state == {}

    def test_empty_dict(self):
        result = extract_state_delta({})
        assert result.app_state_delta == {}
        assert result.user_state_delta == {}
        assert result.session_state == {}

    def test_app_prefix(self):
        result = extract_state_delta({f"{State.APP_PREFIX}key1": "value1"})
        assert result.app_state_delta == {"key1": "value1"}
        assert result.user_state_delta == {}
        assert result.session_state == {}

    def test_user_prefix(self):
        result = extract_state_delta({f"{State.USER_PREFIX}key1": "value1"})
        assert result.app_state_delta == {}
        assert result.user_state_delta == {"key1": "value1"}
        assert result.session_state == {}

    def test_session_state_no_prefix(self):
        result = extract_state_delta({"key1": "value1"})
        assert result.app_state_delta == {}
        assert result.user_state_delta == {}
        assert result.session_state == {"key1": "value1"}

    def test_temp_prefix_ignored(self):
        result = extract_state_delta({f"{State.TEMP_PREFIX}key1": "value1"})
        assert result.app_state_delta == {}
        assert result.user_state_delta == {}
        assert result.session_state == {}

    def test_temp_prefix_not_ignored(self):
        result = extract_state_delta({f"{State.TEMP_PREFIX}key1": "value1"}, ignore_temp=False)
        assert result.session_state == {f"{State.TEMP_PREFIX}key1": "value1"}

    def test_mixed_prefixes(self):
        state_delta = {
            f"{State.APP_PREFIX}app_key": "app_value",
            f"{State.USER_PREFIX}user_key": "user_value",
            f"{State.TEMP_PREFIX}temp_key": "temp_value",
            "session_key": "session_value",
        }
        result = extract_state_delta(state_delta)
        assert result.app_state_delta == {"app_key": "app_value"}
        assert result.user_state_delta == {"user_key": "user_value"}
        assert result.session_state == {"session_key": "session_value"}

    def test_multiple_app_keys(self):
        state_delta = {
            f"{State.APP_PREFIX}k1": "v1",
            f"{State.APP_PREFIX}k2": "v2",
        }
        result = extract_state_delta(state_delta)
        assert result.app_state_delta == {"k1": "v1", "k2": "v2"}


class TestMergeState:
    """Test merge_state function."""

    def test_empty_entry(self):
        entry = StateStorageEntry()
        result = merge_state(entry)
        assert result == {}

    def test_session_state_only(self):
        entry = StateStorageEntry(session_state={"key": "value"})
        result = merge_state(entry)
        assert result == {"key": "value"}

    def test_app_state_merged_with_prefix(self):
        entry = StateStorageEntry(app_state_delta={"app_key": "app_value"})
        result = merge_state(entry)
        assert result == {f"{State.APP_PREFIX}app_key": "app_value"}

    def test_user_state_merged_with_prefix(self):
        entry = StateStorageEntry(user_state_delta={"user_key": "user_value"})
        result = merge_state(entry)
        assert result == {f"{State.USER_PREFIX}user_key": "user_value"}

    def test_all_states_merged(self):
        entry = StateStorageEntry(
            app_state_delta={"a": 1},
            user_state_delta={"b": 2},
            session_state={"c": 3},
        )
        result = merge_state(entry)
        assert result == {f"{State.APP_PREFIX}a": 1, f"{State.USER_PREFIX}b": 2, "c": 3}

    def test_need_copy_true(self):
        original_session_state = {"key": "value"}
        entry = StateStorageEntry(session_state=original_session_state)
        result = merge_state(entry, need_copy=True)
        result["new_key"] = "new_value"
        assert "new_key" not in original_session_state

    def test_need_copy_false(self):
        original_session_state = {"key": "value"}
        entry = StateStorageEntry(session_state=original_session_state)
        result = merge_state(entry, need_copy=False)
        result["new_key"] = "new_value"
        assert "new_key" in original_session_state


class TestKeyFunctions:
    """Test key generation functions."""

    def test_session_key(self):
        assert session_key("app", "user", "sess") == "session:app:user:sess"

    def test_session_key_special_chars(self):
        assert session_key("my-app", "user@domain", "s-1") == "session:my-app:user@domain:s-1"

    def test_app_state_key(self):
        assert app_state_key("my_app") == "app_state:my_app"

    def test_user_state_key(self):
        assert user_state_key("my_app", "user_1") == "user_state:my_app:user_1"

    def test_session_key_empty_strings(self):
        assert session_key("", "", "") == "session:::"

    def test_app_state_key_empty(self):
        assert app_state_key("") == "app_state:"

    def test_user_state_key_empty(self):
        assert user_state_key("", "") == "user_state::"
