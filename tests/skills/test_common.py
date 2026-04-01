# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import json
from unittest.mock import Mock

import pytest
from pydantic import Field
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills._common import BaseSelectionResult
from trpc_agent_sdk.skills._common import SelectionMode
from trpc_agent_sdk.skills._common import add_selection
from trpc_agent_sdk.skills._common import clear_selection
from trpc_agent_sdk.skills._common import generic_get_selection
from trpc_agent_sdk.skills._common import generic_select_items
from trpc_agent_sdk.skills._common import get_previous_selection
from trpc_agent_sdk.skills._common import get_state_delta_value
from trpc_agent_sdk.skills._common import replace_selection
from trpc_agent_sdk.skills._common import set_state_delta_for_selection


class MockSelectionResult(BaseSelectionResult):
    """Mock selection result class for testing."""
    selected_items: list[str] = Field(default_factory=list)
    include_all: bool = Field(default=False)


class TestGetStateDeltaValue:
    """Test suite for get_state_delta_value function."""

    def test_get_from_state_delta(self):
        """Test getting value from state_delta."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {"key1": "value1"}
        mock_ctx.session_state = {"key1": "old_value"}

        result = get_state_delta_value(mock_ctx, "key1")

        assert result == "value1"

    def test_get_from_session_state(self):
        """Test getting value from session_state when not in state_delta."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {"key1": "value1"}

        result = get_state_delta_value(mock_ctx, "key1")

        assert result == "value1"

    def test_get_nonexistent_key(self):
        """Test getting nonexistent key returns None."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {}

        result = get_state_delta_value(mock_ctx, "nonexistent")

        assert result is None


class TestSelectionMode:
    """Test suite for SelectionMode enum."""

    def test_selection_mode_values(self):
        """Test SelectionMode enum values."""
        assert SelectionMode.ADD.value == "add"
        assert SelectionMode.REPLACE.value == "replace"
        assert SelectionMode.CLEAR.value == "clear"

    def test_selection_mode_from_string(self):
        """Test creating SelectionMode from string."""
        assert SelectionMode("add") == SelectionMode.ADD
        assert SelectionMode("replace") == SelectionMode.REPLACE
        assert SelectionMode("clear") == SelectionMode.CLEAR

    def test_selection_mode_invalid_string(self):
        """Test invalid string raises ValueError."""
        with pytest.raises(ValueError):
            SelectionMode("invalid")


class TestBaseSelectionResult:
    """Test suite for BaseSelectionResult class."""

    def test_create_base_selection_result(self):
        """Test creating BaseSelectionResult."""
        result = BaseSelectionResult(skill="test-skill", mode="replace")

        assert result.skill == "test-skill"
        assert result.mode == "replace"

    def test_default_mode(self):
        """Test default mode is empty string."""
        result = BaseSelectionResult(skill="test-skill")

        assert result.mode == ""


class TestGetPreviousSelection:
    """Test suite for get_previous_selection function."""

    def test_get_previous_selection_json(self):
        """Test getting previous selection from JSON."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:docs:test-skill": json.dumps(["doc1.md", "doc2.md"])
        }

        result = get_previous_selection(mock_ctx, "temp:skill:docs:", "test-skill")

        assert result == ["doc1.md", "doc2.md"]

    def test_get_previous_selection_all(self):
        """Test getting previous selection when all items selected."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:docs:test-skill": '*'
        }

        result = get_previous_selection(mock_ctx, "temp:skill:docs:", "test-skill")

        assert result is None

    def test_get_previous_selection_not_found(self):
        """Test getting previous selection when not found."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}

        result = get_previous_selection(mock_ctx, "temp:skill:docs:", "test-skill")

        assert result == []

    def test_get_previous_selection_invalid_json(self):
        """Test getting previous selection with invalid JSON."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:docs:test-skill": "invalid json"
        }

        result = get_previous_selection(mock_ctx, "temp:skill:docs:", "test-skill")

        assert result == []


class TestClearSelection:
    """Test suite for clear_selection function."""

    def test_clear_selection(self):
        """Test clearing selection."""
        result = clear_selection(
            skill_name="test-skill",
            items=["item1"],
            include_all=False,
            previous_items=["item1", "item2"],
            result_class=MockSelectionResult
        )

        assert isinstance(result, MockSelectionResult)
        assert result.skill == "test-skill"
        assert result.mode == "clear"
        assert result.selected_items == []
        assert result.include_all is False


class TestAddSelection:
    """Test suite for add_selection function."""

    def test_add_selection(self):
        """Test adding to selection."""
        result = add_selection(
            skill_name="test-skill",
            items=["item2", "item3"],
            include_all=False,
            previous_items=["item1"],
            result_class=MockSelectionResult
        )

        assert isinstance(result, MockSelectionResult)
        assert result.skill == "test-skill"
        assert result.mode == "add"
        assert set(result.selected_items) == {"item1", "item2", "item3"}
        assert result.include_all is False

    def test_add_selection_duplicates(self):
        """Test adding selection removes duplicates."""
        result = add_selection(
            skill_name="test-skill",
            items=["item1", "item2"],
            include_all=False,
            previous_items=["item1"],
            result_class=MockSelectionResult
        )

        assert len(result.selected_items) == 2
        assert result.selected_items.count("item1") == 1

    def test_add_selection_include_all(self):
        """Test adding selection with include_all."""
        result = add_selection(
            skill_name="test-skill",
            items=["item2"],
            include_all=True,
            previous_items=["item1"],
            result_class=MockSelectionResult
        )

        assert result.selected_items == []
        assert result.include_all is True


class TestReplaceSelection:
    """Test suite for replace_selection function."""

    def test_replace_selection(self):
        """Test replacing selection."""
        result = replace_selection(
            skill_name="test-skill",
            items=["item2", "item3"],
            include_all=False,
            previous_items=["item1"],
            result_class=MockSelectionResult
        )

        assert isinstance(result, MockSelectionResult)
        assert result.skill == "test-skill"
        assert result.mode == "replace"
        assert result.selected_items == ["item2", "item3"]
        assert result.include_all is False

    def test_replace_selection_include_all(self):
        """Test replacing selection with include_all."""
        result = replace_selection(
            skill_name="test-skill",
            items=["item2"],
            include_all=True,
            previous_items=["item1"],
            result_class=MockSelectionResult
        )

        assert result.selected_items == []
        assert result.include_all is True


class TestSetStateDeltaForSelection:
    """Test suite for set_state_delta_for_selection function."""

    def test_set_state_delta_with_items(self):
        """Test setting state delta with items."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = MockSelectionResult(
            skill="test-skill",
            selected_items=["item1", "item2"],
            include_all=False,
            mode="replace"
        )

        set_state_delta_for_selection(mock_ctx, "temp:skill:test:", result)

        key = "temp:skill:test:test-skill"
        assert key in mock_ctx.actions.state_delta
        assert json.loads(mock_ctx.actions.state_delta[key]) == ["item1", "item2"]

    def test_set_state_delta_include_all(self):
        """Test setting state delta with include_all."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = MockSelectionResult(
            skill="test-skill",
            selected_items=[],
            include_all=True,
            mode="replace"
        )

        set_state_delta_for_selection(mock_ctx, "temp:skill:test:", result)

        key = "temp:skill:test:test-skill"
        assert mock_ctx.actions.state_delta[key] == '*'

    def test_set_state_delta_empty_skill(self):
        """Test setting state delta with empty skill name does nothing."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = MockSelectionResult(
            skill="",
            selected_items=["item1"],
            include_all=False,
            mode="replace"
        )

        set_state_delta_for_selection(mock_ctx, "temp:skill:test:", result)

        assert len(mock_ctx.actions.state_delta) == 0


class TestGenericSelectItems:
    """Test suite for generic_select_items function."""

    def test_generic_select_items_replace(self):
        """Test generic select items with replace mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = generic_select_items(
            tool_context=mock_ctx,
            skill_name="test-skill",
            items=["item1", "item2"],
            include_all=False,
            mode="replace",
            state_key_prefix="temp:skill:test:",
            result_class=MockSelectionResult
        )

        assert isinstance(result, MockSelectionResult)
        assert result.skill == "test-skill"
        assert result.mode == "replace"
        assert result.selected_items == ["item1", "item2"]

    def test_generic_select_items_add(self):
        """Test generic select items with add mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": json.dumps(["item1"])
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = generic_select_items(
            tool_context=mock_ctx,
            skill_name="test-skill",
            items=["item2"],
            include_all=False,
            mode="add",
            state_key_prefix="temp:skill:test:",
            result_class=MockSelectionResult
        )

        assert result.mode == "add"
        assert set(result.selected_items) == {"item1", "item2"}

    def test_generic_select_items_clear(self):
        """Test generic select items with clear mode."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": json.dumps(["item1"])
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = generic_select_items(
            tool_context=mock_ctx,
            skill_name="test-skill",
            items=None,
            include_all=False,
            mode="clear",
            state_key_prefix="temp:skill:test:",
            result_class=MockSelectionResult
        )

        assert result.mode == "clear"
        assert result.selected_items == []

    def test_generic_select_items_invalid_mode(self):
        """Test generic select items with invalid mode defaults to replace."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = generic_select_items(
            tool_context=mock_ctx,
            skill_name="test-skill",
            items=["item1"],
            include_all=False,
            mode="invalid",
            state_key_prefix="temp:skill:test:",
            result_class=MockSelectionResult
        )

        assert result.mode == "replace"

    def test_generic_select_items_previous_all(self):
        """Test generic select items when previous was all."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": '*'
        }
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = generic_select_items(
            tool_context=mock_ctx,
            skill_name="test-skill",
            items=["item1"],
            include_all=False,
            mode="add",
            state_key_prefix="temp:skill:test:",
            result_class=MockSelectionResult
        )

        # Should maintain include_all=True
        assert result.include_all is True

    def test_generic_select_items_updates_state(self):
        """Test generic select items updates state delta."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.session_state = {}
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}

        result = generic_select_items(
            tool_context=mock_ctx,
            skill_name="test-skill",
            items=["item1"],
            include_all=False,
            mode="replace",
            state_key_prefix="temp:skill:test:",
            result_class=MockSelectionResult
        )

        key = "temp:skill:test:test-skill"
        assert key in mock_ctx.actions.state_delta


class TestGenericGetSelection:
    """Test suite for generic_get_selection function."""

    def test_generic_get_selection_json_array(self):
        """Test getting selection from JSON array."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": json.dumps(["item1", "item2"])
        }

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:"
        )

        assert result == ["item1", "item2"]

    def test_generic_get_selection_all_with_callback(self):
        """Test getting selection with '*' and callback."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": '*'
        }

        def get_all_items(skill_name):
            return ["item1", "item2", "item3"]

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:",
            get_all_items_callback=get_all_items
        )

        assert result == ["item1", "item2", "item3"]

    def test_generic_get_selection_all_without_callback(self):
        """Test getting selection with '*' but no callback."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": '*'
        }

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:"
        )

        assert result == []

    def test_generic_get_selection_not_found(self):
        """Test getting selection when not found."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {}

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:"
        )

        assert result == []

    def test_generic_get_selection_invalid_json(self):
        """Test getting selection with invalid JSON."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": "invalid json"
        }

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:"
        )

        assert result == []

    def test_generic_get_selection_bytes_value(self):
        """Test getting selection when value is bytes."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": json.dumps(["item1"]).encode('utf-8')
        }

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:"
        )

        assert result == ["item1"]

    def test_generic_get_selection_callback_exception(self):
        """Test getting selection when callback raises exception."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {}
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": '*'
        }

        def get_all_items_error(skill_name):
            raise Exception("Test error")

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:",
            get_all_items_callback=get_all_items_error
        )

        assert result == []

    def test_generic_get_selection_from_state_delta(self):
        """Test getting selection prefers state_delta over session_state."""
        mock_ctx = Mock(spec=InvocationContext)
        mock_ctx.actions = Mock()
        mock_ctx.actions.state_delta = {
            "temp:skill:test:test-skill": json.dumps(["item_new"])
        }
        mock_ctx.session_state = {
            "temp:skill:test:test-skill": json.dumps(["item_old"])
        }

        result = generic_get_selection(
            ctx=mock_ctx,
            skill_name="test-skill",
            state_key_prefix="temp:skill:test:"
        )

        assert result == ["item_new"]

