# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Skill toolset for integrating skills into the agent tool system.

This module provides the SkillToolSet class which makes skills available
as tools to agents.
"""

from __future__ import annotations

import json
from typing import Any
from typing import List
from typing import Literal
from typing import Optional

from pydantic import Field

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from ._common import BaseSelectionResult
from ._common import SelectionMode
from ._common import generic_select_items
from ._constants import SKILL_DOCS_STATE_KEY_PREFIX
from ._constants import SKILL_LOADED_STATE_KEY_PREFIX
from ._constants import SKILL_REPOSITORY_KEY
from ._constants import SKILL_TOOLS_STATE_KEY_PREFIX
from ._repository import BaseSkillRepository


def _set_state_delta(invocation_context: InvocationContext, key: str, value: Any) -> None:
    """Set the state delta of a skill loaded."""
    invocation_context.actions.state_delta[key] = value


def skill_list_docs(tool_context: InvocationContext, skill_name: str) -> List[str]:
    """List doc filenames for a skill.
    Args:
        skill_name: The name of the skill to load.

    Returns:
        A list of the docs of the skill.
    """
    repository: BaseSkillRepository = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    skill = repository.get(skill_name)
    if skill is None:
        logger.error("Skill %s not found", repr(skill_name))
        return []
    return [resource.path for resource in skill.resources]


def skill_list_tools(tool_context: InvocationContext, skill_name: str) -> List[str]:
    """List tool names for a skill.
    Args:
        skill_name: The name of the skill to load.
    Returns:
        A list of the tools of the skill.
    """
    repository: BaseSkillRepository = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    skill = repository.get(skill_name)
    if skill is None:
        logger.error("Skill %s not found", repr(skill_name))
        return []
    return skill.tools


def _set_state_delta_for_skill_load(invocation_context: InvocationContext,
                                    skill_name: str,
                                    docs: list[str],
                                    include_all_docs: bool = False) -> None:
    """Set the state delta of a skill loaded."""
    key = f"{SKILL_LOADED_STATE_KEY_PREFIX}{skill_name}"
    _set_state_delta(invocation_context, key, True)
    key = f"{SKILL_DOCS_STATE_KEY_PREFIX}{skill_name}"
    if include_all_docs:
        _set_state_delta(invocation_context, key, '*')
    else:
        _set_state_delta(invocation_context, key, json.dumps(docs or []))


def _set_state_delta_for_skill_tools(invocation_context: InvocationContext, skill_name: str, tools: list[str]) -> None:
    """Set the state delta of a skill tools."""
    key = f"{SKILL_TOOLS_STATE_KEY_PREFIX}{skill_name}"
    _set_state_delta(invocation_context, key, json.dumps(tools or []))


def skill_load(tool_context: InvocationContext,
               skill_name: str,
               docs: Optional[list[str]] = None,
               include_all_docs: bool = False) -> str:
    """Load a skill body and optional docs. Safe to call multiple times to add or replace docs.
    Do not call this to list skills; names and descriptions are already in context.
    Use when a task needs a skill's SKILL.md body and selected docs in context.
    Args:
        skill_name: The name of the skill to load.
        docs: The docs of the skill to load.
        include_all_docs: Whether to include all docs of the skill.

    Returns:
        A message indicating the skill was loaded.
    """

    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    skill = repository.get(skill_name)
    if skill is None:
        return f"skill {skill_name!r} not found"
    _set_state_delta_for_skill_load(tool_context, skill_name, docs or [], include_all_docs)
    if skill.tools:
        _set_state_delta_for_skill_tools(tool_context, skill_name, skill.tools)
    return f"skill {skill_name!r} loaded"


def skill_list(tool_context: InvocationContext) -> List[str]:
    """List all available skills.
    Args:
        tool_context: The tool context.
    Returns:
        A list of all available skills.
    """
    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    return repository.skill_list()


# Alias for backward compatibility
SkillSelectDocsMode = SelectionMode


class SkillSelectDocsResult(BaseSelectionResult):
    """Result for selecting docs of a skill."""
    selected_docs: list[str] = Field(default_factory=list, description="The selected docs of the skill.")
    include_all_docs: bool = Field(default=False, description="Whether to include all docs of the skill.")

    # Accept alias fields during initialization (excluded from serialization)
    selected_items: list[str] = Field(default=None, exclude=True, repr=False)
    include_all: bool = Field(default=None, exclude=True, repr=False)

    def model_post_init(self, __context) -> None:
        """Handle alias fields after model initialization."""
        # If selected_items was explicitly provided, use it to set selected_docs
        if self.selected_items is not None:
            self.selected_docs = self.selected_items
        # If include_all was explicitly provided, use it to set include_all_docs
        if self.include_all is not None:
            self.include_all_docs = self.include_all


def skill_select_docs(tool_context: InvocationContext,
                      skill_name: str,
                      docs: Optional[list[str]] = None,
                      include_all_docs: bool = False,
                      mode: Literal['add', 'replace', 'clear'] = "replace") -> SkillSelectDocsResult:
    """Select docs for a skill. Use mode=add to append, replace to overwrite, or clear to remove.
    Args:
        skill_name: The name of the skill to select the docs of.
        docs: The docs of the skill to select the docs of.
        include_all_docs: Whether to include all docs of the skill.
        mode: The mode to use for selecting the docs of the skill.

    Returns:
        A message indicating the docs were selected.
    """
    result = generic_select_items(tool_context=tool_context,
                                  skill_name=skill_name,
                                  items=docs,
                                  include_all=include_all_docs,
                                  mode=mode,
                                  state_key_prefix=SKILL_DOCS_STATE_KEY_PREFIX,
                                  result_class=SkillSelectDocsResult)
    return result


# ==================== Tool Selection ====================

# Alias for backward compatibility
SkillSelectToolsMode = SelectionMode


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
