# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Select tools for a skill. Use mode=add to append, replace to overwrite, or clear to remove.
"""

from __future__ import annotations

from typing import Literal
from typing import Optional

from pydantic import Field
from trpc_agent_sdk.context import InvocationContext

from .._common import BaseSelectionResult
from .._common import append_loaded_order_state_delta
from .._common import get_agent_name
from .._common import get_previous_selection_by_key
from .._common import normalize_selection_mode
from .._common import set_selection_state_delta_by_key
from .._common import tool_state_key
from .._constants import SKILL_REPOSITORY_KEY
from .._repository import BaseSkillRepository


class SkillSelectToolsResult(BaseSelectionResult):
    """Result for selecting tools of a skill."""
    selected_tools: list[str] = Field(default_factory=list, description="The selected tools of the skill")
    include_all_tools: bool = Field(default=False, description="Whether to include all tools of the skill")

    # Accept alias fields during initialization (excluded from serialization)
    selected_items: list[str] = Field(default=None, exclude=True, repr=False)
    include_all: bool = Field(default=None, exclude=True, repr=False)

    def model_post_init(self, __context) -> None:
        """Handle alias fields after model initialization."""
        # If selected_items was explicitly provided, use it to set selected_tools
        if self.selected_items is not None:
            self.selected_tools = self.selected_items
        # If include_all was explicitly provided, use it to set include_all_tools
        if self.include_all is not None:
            self.include_all_tools = self.include_all


def skill_select_tools(tool_context: InvocationContext,
                       skill_name: str,
                       tools: Optional[list[str]] = None,
                       include_all_tools: bool = False,
                       mode: Literal['add', 'replace', 'clear'] = "replace") -> SkillSelectToolsResult:
    """Select tools for a skill. Use mode=add to append, replace to overwrite, or clear to remove.

    This function allows dynamic tool selection after a skill is loaded. You can:
    - Add more tools from the skill's available tools
    - Replace the current tool selection
    - Clear all selected tools

    Args:
        skill_name: The name of the skill to select tools for
        tools: List of tool names to select (must be defined in skill's SKILL.md)
        include_all_tools: Whether to include all tools defined in the skill
        mode: Selection mode - 'add', 'replace', or 'clear'

    Returns:
        SkillSelectToolsResult with selection details

    Example:
        # Load skill with default tools
        skill_load(skill_name="weather-tools")

        # Add more tools
        skill_select_tools(skill_name="weather-tools",
                          tools=["get_weather_alerts"],
                          mode="add")

        # Replace with specific tools only
        skill_select_tools(skill_name="weather-tools",
                          tools=["get_current_weather"],
                          mode="replace")
    """
    normalized_skill = (skill_name or "").strip()
    if not normalized_skill:
        raise ValueError("skill is required")
    normalized_mode = normalize_selection_mode(mode)
    agent_name = get_agent_name(tool_context)

    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is not None:
        try:
            _ = repository.get(normalized_skill)
        except ValueError as ex:
            raise ValueError(f"unknown skill: {normalized_skill}") from ex

    tools_selection_key = tool_state_key(tool_context, normalized_skill)
    previous_items, had_all = get_previous_selection_by_key(tool_context, tools_selection_key)
    if had_all and normalized_mode != "clear":
        result = SkillSelectToolsResult(
            skill=normalized_skill,
            selected_items=[],
            include_all=True,
            mode=normalized_mode,
        )
    elif normalized_mode == "clear":
        result = SkillSelectToolsResult(
            skill=normalized_skill,
            selected_items=[],
            include_all=False,
            mode="clear",
        )
    elif normalized_mode == "add":
        selected = set(previous_items)
        for item in tools or []:
            selected.add(item)
        result = SkillSelectToolsResult(
            skill=normalized_skill,
            selected_items=[] if include_all_tools else list(selected),
            include_all=include_all_tools,
            mode="add",
        )
    else:
        result = SkillSelectToolsResult(
            skill=normalized_skill,
            selected_items=[] if include_all_tools else list(tools or []),
            include_all=include_all_tools,
            mode="replace",
        )

    set_selection_state_delta_by_key(
        tool_context,
        tools_selection_key,
        result.selected_tools,
        result.include_all_tools,
    )
    append_loaded_order_state_delta(tool_context, agent_name, normalized_skill)
    return result
