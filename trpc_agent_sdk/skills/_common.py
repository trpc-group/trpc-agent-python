# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Common utilities for skill selection operations.

This module provides generic functions for selecting skill resources (docs, tools, etc.)
with add/replace/clear modes.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any
from typing import Callable
from typing import Literal
from typing import Optional
from typing import TypeVar

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

# Generic type for selection results
T = TypeVar('T', bound=BaseModel)  # pylint: disable=invalid-name


def get_state_delta_value(invocation_context: InvocationContext, key: str) -> Any:
    """Get a value from state delta, checking both session state and current delta.

    Args:
        invocation_context: Invocation context
        key: State key

    Returns:
        Value from state delta, or None if not found
    """
    # First check current state delta
    if key in invocation_context.actions.state_delta:
        return invocation_context.actions.state_delta[key]

    # Fall back to session state
    return invocation_context.session_state.get(key, None)


class SelectionMode(str, Enum):
    """Mode for selecting resources."""
    ADD = "add"
    REPLACE = "replace"
    CLEAR = "clear"


class BaseSelectionResult(BaseModel):
    """Base result for selection operations."""
    skill: str = Field(..., description="The name of the skill")
    mode: str = Field(default="", description="The mode used for selecting")


def _set_state_delta(invocation_context: InvocationContext, key: str, value: Any) -> None:
    """Set the state delta for a key.

    Args:
        invocation_context: Invocation context
        key: State key
        value: State value
    """
    invocation_context.actions.state_delta[key] = value


def get_previous_selection(invocation_context: InvocationContext, state_key_prefix: str,
                           skill_name: str) -> Optional[list[str]]:
    """Get the previous selection for a skill from session state.

    Args:
        invocation_context: Invocation context
        state_key_prefix: State key prefix (e.g., "temp:skill:docs:", "temp:skill:tools:")
        skill_name: Skill name

    Returns:
        List of selected items, or None if include_all was set (represented by '*')
    """
    key = f"{state_key_prefix}{skill_name}"
    value = invocation_context.session_state.get(key, None)
    if not value:
        return []
    if value == '*':
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


def clear_selection(skill_name: str, items: list[str], include_all: bool, previous_items: list[str],
                    result_class: type[T]) -> T:
    """Clear the selection for a skill.

    Args:
        skill_name: Skill name
        items: Items to select (ignored for clear)
        include_all: Whether to include all items (ignored for clear)
        previous_items: Previous selection (ignored for clear)
        result_class: Result class to instantiate

    Returns:
        Selection result with empty selection
    """
    return result_class(
        skill=skill_name,
        selected_items=[],
        include_all=False,
        mode="clear",
    )


def add_selection(skill_name: str, items: list[str], include_all: bool, previous_items: list[str],
                  result_class: type[T]) -> T:
    """Add items to the current selection for a skill.

    Args:
        skill_name: Skill name
        items: Items to add
        include_all: Whether to include all items
        previous_items: Previous selection
        result_class: Result class to instantiate

    Returns:
        Selection result with added items
    """
    selected = set(previous_items)
    for item in items:
        selected.add(item)

    if include_all:
        selected_list = []
    else:
        selected_list = list(selected)

    return result_class(
        skill=skill_name,
        selected_items=selected_list,
        include_all=include_all,
        mode="add",
    )


def replace_selection(skill_name: str, items: list[str], include_all: bool, previous_items: list[str],
                      result_class: type[T]) -> T:
    """Replace the current selection for a skill.

    Args:
        skill_name: Skill name
        items: Items to select
        include_all: Whether to include all items
        previous_items: Previous selection (ignored for replace)
        result_class: Result class to instantiate

    Returns:
        Selection result with replaced items
    """
    if include_all:
        selected = []
    else:
        selected = items

    return result_class(
        skill=skill_name,
        selected_items=selected,
        include_all=include_all,
        mode="replace",
    )


def set_state_delta_for_selection(invocation_context: InvocationContext, state_key_prefix: str,
                                  result: BaseModel) -> None:
    """Set the state delta for a selection result.

    Args:
        invocation_context: Invocation context
        state_key_prefix: State key prefix (e.g., "temp:skill:docs:", "temp:skill:tools:")
        result: Selection result (must have 'skill', 'selected_items', 'include_all' attributes)
    """
    skill = getattr(result, 'skill', None)
    if not skill:
        return

    key = f"{state_key_prefix}{skill}"
    include_all = getattr(result, 'include_all', False)

    if include_all:
        _set_state_delta(invocation_context, key, '*')
        return

    selected_items = getattr(result, 'selected_items', [])
    selected_json = json.dumps(selected_items)
    _set_state_delta(invocation_context, key, selected_json)


def generic_select_items(tool_context: InvocationContext, skill_name: str, items: Optional[list[str]],
                         include_all: bool, mode: Literal['add', 'replace',
                                                          'clear'], state_key_prefix: str, result_class: type[T]) -> T:
    """Generic function for selecting items (docs, tools, etc.) for a skill.

    This is a generic implementation that can be used for selecting any type of
    skill resource (docs, tools, etc.) with add/replace/clear modes.

    Args:
        tool_context: Invocation context
        skill_name: Name of the skill
        items: List of item names to select
        include_all: Whether to include all items
        mode: Selection mode - 'add', 'replace', or 'clear'
        state_key_prefix: State key prefix (e.g., "temp:skill:docs:")
        result_class: Result class to instantiate (must have 'skill', 'selected_items',
                     'include_all', 'mode' fields)

    Returns:
        Selection result instance

    Example:
        # For docs:
        result = generic_select_items(
            tool_context=ctx,
            skill_name="my-skill",
            items=["doc1.md", "doc2.md"],
            include_all=False,
            mode="replace",
            state_key_prefix="temp:skill:docs:",
            result_class=SkillSelectDocsResult
        )

        # For tools:
        result = generic_select_items(
            tool_context=ctx,
            skill_name="my-skill",
            items=["tool1", "tool2"],
            include_all=False,
            mode="replace",
            state_key_prefix="temp:skill:tools:",
            result_class=SkillSelectToolsResult
        )
    """
    # Parse mode
    try:
        mode_enum = SelectionMode(mode)
    except ValueError:
        mode_enum = SelectionMode.REPLACE

    # Get previous selection
    previous_items = get_previous_selection(tool_context, state_key_prefix, skill_name)

    # Handle special case: if previous was '*' (include_all) and not clearing
    if previous_items is None and mode_enum != SelectionMode.CLEAR:
        result = result_class(
            skill=skill_name,
            selected_items=[],
            include_all=True,
            mode=mode_enum.value,
        )
    else:
        # Map mode to function
        mode_to_func = {
            SelectionMode.CLEAR: clear_selection,
            SelectionMode.ADD: add_selection,
            SelectionMode.REPLACE: replace_selection,
        }

        func = mode_to_func[mode_enum]
        result = func(skill_name=skill_name,
                      items=items or [],
                      include_all=include_all,
                      previous_items=previous_items or [],
                      result_class=result_class)

    # Update state delta
    set_state_delta_for_selection(tool_context, state_key_prefix, result)

    return result


def generic_get_selection(ctx: InvocationContext,
                          skill_name: str,
                          state_key_prefix: str,
                          get_all_items_callback: Optional[Callable[[str], list[str]]] = None) -> list[str]:
    """Generic function to get selection (docs, tools, etc.) for a skill from state.

    This function handles the common pattern of:
    1. Getting a value from state using a key prefix + skill name
    2. If value is "*", calling a callback to get all items
    3. If value is a JSON array, parsing and returning it

    Args:
        ctx: Invocation context
        skill_name: Name of the skill
        state_key_prefix: State key prefix (e.g., "temp:skill:docs:", "temp:skill:tools:")
        get_all_items_callback: Optional callback to get all items when value is "*".
                               Should accept skill_name and return list[str].
                               If None, returns empty list for "*".

    Returns:
        List of selected item names

    Example:
        # For docs:
        def get_all_docs(skill_name):
            skill = repository.get(skill_name)
            return [d.path for d in skill.resources]

        docs = generic_get_selection(
            ctx=ctx,
            skill_name="my-skill",
            state_key_prefix="temp:skill:docs:",
            get_all_items_callback=get_all_docs
        )

        # For tools:
        def get_all_tools(skill_name):
            skill = repository.get(skill_name)
            return skill.tools

        tools = generic_get_selection(
            ctx=ctx,
            skill_name="my-skill",
            state_key_prefix="temp:skill:tools:",
            get_all_items_callback=get_all_tools
        )
    """
    key = state_key_prefix + skill_name
    v = get_state_delta_value(ctx, key)

    if not v:
        return []

    # Convert to string for processing
    v_str = v.decode('utf-8') if isinstance(v, bytes) else str(v)

    # Handle "*" (select all)
    if v_str == "*":
        if get_all_items_callback is not None:
            try:
                return get_all_items_callback(skill_name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get all items for skill '%s': %s", skill_name, ex)
                return []
        return []

    # Handle JSON array
    try:
        arr = json.loads(v_str)
        return arr if isinstance(arr, list) else []
    except json.JSONDecodeError:
        logger.warning("Failed to parse selection for skill '%s' with key '%s': %s", skill_name, key, v_str)
        return []
