# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills._common.

Covers:
- SelectionMode enum
- BaseSelectionResult model
- get_state_delta_value
- get_previous_selection
- clear_selection, add_selection, replace_selection
- set_state_delta_for_selection
- generic_select_items (all modes, edge cases)
- generic_get_selection (all branches)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import BaseModel, Field

from trpc_agent_sdk.skills._common import (
    BaseSelectionResult,
    SelectionMode,
    add_selection,
    clear_selection,
    generic_get_selection,
    generic_select_items,
    get_previous_selection,
    get_state_delta_value,
    replace_selection,
    set_state_delta_for_selection,
)


class _TestSelectionResult(BaseSelectionResult):
    """Concrete test subclass for BaseSelectionResult."""
    selected_items: list[str] = Field(default_factory=list)
    include_all: bool = Field(default=False)


def _make_ctx(state_delta=None, session_state=None):
    ctx = MagicMock()
    ctx.actions.state_delta = state_delta or {}
    ctx.session_state = session_state or {}
    return ctx


# ---------------------------------------------------------------------------
# SelectionMode
# ---------------------------------------------------------------------------

class TestSelectionMode:
    def test_values(self):
        assert SelectionMode.ADD == "add"
        assert SelectionMode.REPLACE == "replace"
        assert SelectionMode.CLEAR == "clear"

    def test_from_string(self):
        assert SelectionMode("add") == SelectionMode.ADD


# ---------------------------------------------------------------------------
# get_state_delta_value
# ---------------------------------------------------------------------------

class TestGetStateDeltaValue:
    def test_from_state_delta(self):
        ctx = _make_ctx(state_delta={"key": "delta_value"})
        assert get_state_delta_value(ctx, "key") == "delta_value"

    def test_from_session_state(self):
        ctx = _make_ctx(session_state={"key": "session_value"})
        assert get_state_delta_value(ctx, "key") == "session_value"

    def test_delta_takes_precedence(self):
        ctx = _make_ctx(state_delta={"k": "delta"}, session_state={"k": "session"})
        assert get_state_delta_value(ctx, "k") == "delta"

    def test_missing_returns_none(self):
        ctx = _make_ctx()
        assert get_state_delta_value(ctx, "missing") is None


# ---------------------------------------------------------------------------
# get_previous_selection
# ---------------------------------------------------------------------------

class TestGetPreviousSelection:
    def test_no_value_returns_empty_list(self):
        ctx = _make_ctx()
        result = get_previous_selection(ctx, "prefix:", "skill")
        assert result == []

    def test_star_returns_none(self):
        ctx = _make_ctx(session_state={"prefix:skill": "*"})
        result = get_previous_selection(ctx, "prefix:", "skill")
        assert result is None

    def test_json_array(self):
        ctx = _make_ctx(session_state={"prefix:skill": json.dumps(["a", "b"])})
        result = get_previous_selection(ctx, "prefix:", "skill")
        assert result == ["a", "b"]

    def test_invalid_json_returns_empty(self):
        ctx = _make_ctx(session_state={"prefix:skill": "not json"})
        result = get_previous_selection(ctx, "prefix:", "skill")
        assert result == []

    def test_empty_string_returns_empty(self):
        ctx = _make_ctx(session_state={"prefix:skill": ""})
        result = get_previous_selection(ctx, "prefix:", "skill")
        assert result == []


# ---------------------------------------------------------------------------
# clear_selection
# ---------------------------------------------------------------------------

class TestClearSelection:
    def test_clear(self):
        result = clear_selection("skill", ["a", "b"], True, ["old"], _TestSelectionResult)
        assert result.skill == "skill"
        assert result.selected_items == []
        assert result.include_all is False
        assert result.mode == "clear"


# ---------------------------------------------------------------------------
# add_selection
# ---------------------------------------------------------------------------

class TestAddSelection:
    def test_add_to_empty(self):
        result = add_selection("skill", ["a", "b"], False, [], _TestSelectionResult)
        assert set(result.selected_items) == {"a", "b"}
        assert result.include_all is False
        assert result.mode == "add"

    def test_add_to_existing(self):
        result = add_selection("skill", ["c"], False, ["a", "b"], _TestSelectionResult)
        assert set(result.selected_items) == {"a", "b", "c"}

    def test_add_deduplicate(self):
        result = add_selection("skill", ["a"], False, ["a"], _TestSelectionResult)
        assert result.selected_items == ["a"]

    def test_add_include_all(self):
        result = add_selection("skill", ["a"], True, ["b"], _TestSelectionResult)
        assert result.selected_items == []
        assert result.include_all is True


# ---------------------------------------------------------------------------
# replace_selection
# ---------------------------------------------------------------------------

class TestReplaceSelection:
    def test_replace(self):
        result = replace_selection("skill", ["x", "y"], False, ["old"], _TestSelectionResult)
        assert result.selected_items == ["x", "y"]
        assert result.mode == "replace"

    def test_replace_include_all(self):
        result = replace_selection("skill", ["x"], True, [], _TestSelectionResult)
        assert result.selected_items == []
        assert result.include_all is True


# ---------------------------------------------------------------------------
# set_state_delta_for_selection
# ---------------------------------------------------------------------------

class TestSetStateDeltaForSelection:
    def test_sets_json_array(self):
        ctx = _make_ctx()
        result = _TestSelectionResult(skill="s", selected_items=["a", "b"], include_all=False)
        set_state_delta_for_selection(ctx, "prefix:", result)
        assert json.loads(ctx.actions.state_delta["prefix:s"]) == ["a", "b"]

    def test_sets_star_for_include_all(self):
        ctx = _make_ctx()
        result = _TestSelectionResult(skill="s", selected_items=[], include_all=True)
        set_state_delta_for_selection(ctx, "prefix:", result)
        assert ctx.actions.state_delta["prefix:s"] == "*"

    def test_no_skill_is_noop(self):
        ctx = _make_ctx()
        result = _TestSelectionResult(skill="", selected_items=[])
        set_state_delta_for_selection(ctx, "prefix:", result)
        assert len(ctx.actions.state_delta) == 0


# ---------------------------------------------------------------------------
# generic_select_items
# ---------------------------------------------------------------------------

class TestGenericSelectItems:
    def test_replace_mode(self):
        ctx = _make_ctx()
        result = generic_select_items(
            ctx, "skill", ["a", "b"], False, "replace", "prefix:", _TestSelectionResult
        )
        assert result.selected_items == ["a", "b"]
        assert result.mode == "replace"

    def test_add_mode(self):
        ctx = _make_ctx(session_state={"prefix:skill": json.dumps(["a"])})
        result = generic_select_items(
            ctx, "skill", ["b"], False, "add", "prefix:", _TestSelectionResult
        )
        assert set(result.selected_items) == {"a", "b"}
        assert result.mode == "add"

    def test_clear_mode(self):
        ctx = _make_ctx(session_state={"prefix:skill": json.dumps(["a"])})
        result = generic_select_items(
            ctx, "skill", [], False, "clear", "prefix:", _TestSelectionResult
        )
        assert result.selected_items == []
        assert result.mode == "clear"

    def test_invalid_mode_defaults_to_replace(self):
        ctx = _make_ctx()
        result = generic_select_items(
            ctx, "skill", ["x"], False, "invalid_mode", "prefix:", _TestSelectionResult
        )
        assert result.mode == "replace"

    def test_previous_star_and_not_clearing_keeps_include_all(self):
        ctx = _make_ctx(session_state={"prefix:skill": "*"})
        result = generic_select_items(
            ctx, "skill", ["a"], False, "add", "prefix:", _TestSelectionResult
        )
        assert result.include_all is True

    def test_previous_star_and_clear(self):
        ctx = _make_ctx(session_state={"prefix:skill": "*"})
        result = generic_select_items(
            ctx, "skill", [], False, "clear", "prefix:", _TestSelectionResult
        )
        assert result.include_all is False
        assert result.selected_items == []

    def test_none_items_treated_as_empty(self):
        ctx = _make_ctx()
        result = generic_select_items(
            ctx, "skill", None, False, "replace", "prefix:", _TestSelectionResult
        )
        assert result.selected_items == []

    def test_updates_state_delta(self):
        ctx = _make_ctx()
        generic_select_items(
            ctx, "skill", ["a"], False, "replace", "prefix:", _TestSelectionResult
        )
        assert "prefix:skill" in ctx.actions.state_delta


# ---------------------------------------------------------------------------
# generic_get_selection
# ---------------------------------------------------------------------------

class TestGenericGetSelection:
    def test_no_value_returns_empty(self):
        ctx = _make_ctx()
        assert generic_get_selection(ctx, "skill", "prefix:") == []

    def test_json_array(self):
        ctx = _make_ctx(state_delta={"prefix:skill": json.dumps(["a", "b"])})
        assert generic_get_selection(ctx, "skill", "prefix:") == ["a", "b"]

    def test_star_with_callback(self):
        ctx = _make_ctx(state_delta={"prefix:skill": "*"})
        callback = Mock(return_value=["all_a", "all_b"])
        result = generic_get_selection(ctx, "skill", "prefix:", callback)
        assert result == ["all_a", "all_b"]
        callback.assert_called_once_with("skill")

    def test_star_without_callback(self):
        ctx = _make_ctx(state_delta={"prefix:skill": "*"})
        assert generic_get_selection(ctx, "skill", "prefix:") == []

    def test_star_callback_exception_returns_empty(self):
        ctx = _make_ctx(state_delta={"prefix:skill": "*"})
        callback = Mock(side_effect=RuntimeError("boom"))
        assert generic_get_selection(ctx, "skill", "prefix:", callback) == []

    def test_invalid_json_returns_empty(self):
        ctx = _make_ctx(state_delta={"prefix:skill": "not_json"})
        assert generic_get_selection(ctx, "skill", "prefix:") == []

    def test_bytes_value_decoded(self):
        ctx = _make_ctx(state_delta={"prefix:skill": json.dumps(["x"]).encode("utf-8")})
        assert generic_get_selection(ctx, "skill", "prefix:") == ["x"]

    def test_bytes_star_with_callback(self):
        ctx = _make_ctx(state_delta={"prefix:skill": b"*"})
        callback = Mock(return_value=["all"])
        result = generic_get_selection(ctx, "skill", "prefix:", callback)
        assert result == ["all"]

    def test_non_list_json_returns_empty(self):
        ctx = _make_ctx(state_delta={"prefix:skill": json.dumps({"not": "list"})})
        assert generic_get_selection(ctx, "skill", "prefix:") == []
