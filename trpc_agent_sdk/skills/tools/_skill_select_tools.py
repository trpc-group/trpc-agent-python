# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Select tools for a skill. Use mode=add to append, replace to overwrite, or clear to remove.
"""

from __future__ import annotations

from typing import Literal
from typing import Optional

from pydantic import Field
from trpc_agent_sdk.context import InvocationContext

from .._common import BaseSelectionResult
from .._common import generic_select_items
from .._constants import SKILL_TOOLS_STATE_KEY_PREFIX


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
    result = generic_select_items(tool_context=tool_context,
                                  skill_name=skill_name,
                                  items=tools,
                                  include_all=include_all_tools,
                                  mode=mode,
                                  state_key_prefix=SKILL_TOOLS_STATE_KEY_PREFIX,
                                  result_class=SkillSelectToolsResult)
    return result
