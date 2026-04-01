# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Dynamic skill toolset for loading tools from skills.

This module provides DynamicSkillToolSet which dynamically loads tools
from skills when they are loaded, enabling token-efficient tool management.
"""

from __future__ import annotations

import json
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import ToolType
from trpc_agent_sdk.tools import get_tool
from trpc_agent_sdk.tools import get_tool_set

from ._constants import SKILL_LOADED_STATE_KEY_PREFIX
from ._constants import SKILL_TOOLS_STATE_KEY_PREFIX
from ._repository import BaseSkillRepository
from ._utils import get_state_delta


class DynamicSkillToolSet(BaseToolSet):
    """ToolSet that dynamically loads tools based on skill selections.

    This toolset monitors skill loading state and tool selection state, then dynamically
    provides only the tools that are selected for loaded skills. This approach saves tokens
    by only including relevant tool definitions in the LLM context.

    Workflow:
        1. Initially, only skill descriptions are visible to the LLM
        2. When LLM calls skill_load(skill="skill-name"), the skill is loaded
        3. The skill's SKILL.md defines which tools it needs (in Tools: section)
        4. DynamicSkillToolSet detects the loaded skill and selected tools
        5. Only selected tools become available to the LLM
        6. LLM can refine selection with skill_select_tools

    Example SKILL.md:
        ---
        name: file-tools
        description: File operations
        ---

        Tools:
        - get_weather
        - get_data

        Overview...

    Usage:
        # Method 1: Define tools as dictionary
        all_tools = {
            "get_weather": GetWeatherTool(),
            "get_data": GetDataTool(),
            "read_file": ReadFileTool(),
        }

        # Method 2: Use ToolSets
        available_tools = [
            WeatherToolSet(),
            FileToolSet(),
            # ... more toolsets
        ]

        # Method 3: Mixed
        available_tools = {
            "tool1": Tool1(),
            "tool2": Tool2(),
            "__toolsets__": [WeatherToolSet(), FileToolSet()]
        }

        # Create dynamic toolset
        dynamic_toolset = DynamicSkillToolSet(
            skill_repository=skill_repository,
            available_tools=all_tools,
            only_active_skills=True  # Only load tools from recently activated skills
        )

        # Configure agent
        agent = LlmAgent(
            name="my_agent",
            tools=[skill_toolset, dynamic_toolset],
            skill_repository=skill_repository
        )

    Attributes:
        _skill_repository: Repository to access skill information
        _available_tools: Dictionary mapping tool names to tool instances
        _available_toolsets: List of toolsets to search for tools
        _tool_cache: Cache of resolved tools
        _only_active_skills: Whether to only return tools from skills active in current turn
    """

    def __init__(self,
                 skill_repository: BaseSkillRepository,
                 available_tools: Optional[List[ToolType]] = None,
                 only_active_skills: bool = True):
        """Initialize DynamicSkillToolSet.

        Args:
            skill_repository: Skill repository to access skill paths and definitions
            available_tools: Can be:
                - List[ToolType]: List of tools/toolsets (can be str, BaseTool or BaseToolSet)
                - None: Use global registry only
            only_active_skills: If True, only return tools from skills that were loaded/modified
                              in the current turn (state_delta). If False, return tools from
                              all loaded skills. Default: True (recommended for token efficiency)
        """
        super().__init__(name="dynamic_skill_tools")
        self._skill_repository = skill_repository
        self._available_tools: Dict[str, BaseTool] = {}
        self._available_toolsets: List[BaseToolSet] = []
        self._tool_cache: Dict[str, BaseTool] = {}
        self._only_active_skills = only_active_skills

        # Parse available_tools parameter
        for tool in available_tools or []:
            if isinstance(tool, str):
                self._find_tool_by_name(tool)
            else:
                self._find_tool_by_type(tool)

        logger.info("DynamicSkillToolSet initialized: %s tools, %s toolsets, only_active_skills=%s",
                    len(self._available_tools), len(self._available_toolsets), only_active_skills)
        logger.info("DynamicSkillToolSet initialized: %s tools, %s toolsets, only_active_skills=%s",
                    len(self._available_tools), len(self._available_toolsets), only_active_skills)

    def _find_tool_by_name(self, tool_name: str) -> bool:
        """Find a tool from available tools."""
        tool = get_tool(tool_name)
        if tool:
            self._available_tools[tool_name] = tool
            return True
        tool_set = get_tool_set(tool_name)
        if tool_set:
            self._available_toolsets.append(tool_set)
            return True
        return False

    def _find_tool_by_type(self, tool: ToolType) -> bool:
        """Find a tool from available toolsets."""
        if isinstance(tool, BaseTool):
            self._available_tools[tool.name] = tool
            return True
        if isinstance(tool, BaseToolSet):
            self._available_toolsets.append(tool)
            return True
        return False

    async def _resolve_tool(self, tool_name: str, ctx: Optional[InvocationContext] = None) -> Optional[BaseTool]:
        """Resolve a tool by name from available sources.

        This method searches for a tool in the following order:
        1. _available_tools dictionary
        2. _available_toolsets (if ctx provided)
        3. _tool_cache
        4. Global tool registry

        Args:
            tool_name: Name of the tool to resolve
            ctx: Invocation context (needed for toolset resolution)

        Returns:
            BaseTool instance if found, None otherwise
        """
        # Check cache first
        if tool_name in self._tool_cache:
            return self._tool_cache[tool_name]

        # Check available_tools
        if tool_name in self._available_tools:
            tool = self._available_tools[tool_name]
            self._tool_cache[tool_name] = tool
            return tool

        # Check available_toolsets
        for toolset in self._available_toolsets:
            try:
                tools: list[BaseTool] = await toolset.get_tools(ctx)
                for tool in tools:
                    # Cache all tools from this toolset
                    self._tool_cache[tool.name] = tool
                    if tool.name == tool_name:
                        return tool
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get tools from toolset '%s': %s", toolset.name, ex)
                logger.warning("Failed to get tools from toolset '%s': %s", toolset.name, ex)

        # Fallback to global registry
        tool = get_tool(tool_name)
        if tool:
            self._tool_cache[tool_name] = tool
            return tool

        return None

    @override
    async def get_tools(self, ctx: InvocationContext) -> List[BaseTool]:
        """Get tools based on loaded skills and their tool selections.

        This method:
        1. Determines which skills to consider (active or all loaded)
        2. For each skill, gets the selected tools from session state
        3. Resolves tool names to tool instances from available sources
        4. Returns only unique, resolved tools

        Behavior depends on only_active_skills flag:
        - If True: Only returns tools from skills that were loaded/modified in current turn
          (state_delta). This is more token-efficient as it focuses on the current context.
        - If False: Returns tools from all loaded skills in the session.

        Args:
            ctx: Invocation context containing session state

        Returns:
            List of selected tools from relevant skills
        """
        # Determine which skills to process
        if self._only_active_skills:
            # Only get skills that are active in current turn (in state_delta)
            skills_to_process = self._get_active_skills_from_delta(ctx)
            if skills_to_process:
                logger.debug("Processing active skills from current turn: %s", skills_to_process)
            else:
                # Fallback: if no active skills, consider all loaded skills
                # This handles the case where skills were loaded in previous turns
                skills_to_process = self._get_loaded_skills_from_state(ctx)
                if skills_to_process:
                    logger.debug("No active skills in current turn, using all loaded skills: %s", skills_to_process)
        else:
            # Get all loaded skills from session
            skills_to_process = self._get_loaded_skills_from_state(ctx)
            logger.debug("Processing all loaded skills: %s", skills_to_process)

        if not skills_to_process:
            logger.debug("No skills to process, returning empty tool list")
            return []

        selected_tools: List[BaseTool] = []
        selected_tool_names: set[str] = set()

        for skill_name in skills_to_process:
            # Get tool selection for this skill
            tool_names: list[str] = self._get_tools_selection(ctx, skill_name)

            if not tool_names:
                logger.debug("No tools selected for skill '%s'", skill_name)
                logger.debug("No tools selected for skill '%s'", skill_name)
                continue

            logger.debug("Skill '%s' requires tools: %s", skill_name, tool_names)
            logger.debug("Skill '%s' requires tools: %s", skill_name, tool_names)

            # Resolve tool names to tool instances
            for tool_name in tool_names:
                # Skip if already added (avoid duplicates)
                if tool_name in selected_tool_names:
                    continue

                # Resolve tool from available sources
                tool = await self._resolve_tool(tool_name, ctx)
                if tool is None:
                    logger.warning(
                        "Tool '%s' required by skill '%s' could not be resolved. Checked: available_tools (%s), available_toolsets (%s), global registry",
                        tool_name, skill_name, len(self._available_tools), len(self._available_toolsets))
                    continue

                selected_tools.append(tool)
                selected_tool_names.add(tool_name)
                logger.debug("Resolved tool '%s' for skill '%s'", tool_name, skill_name)
                logger.debug("Resolved tool '%s' for skill '%s'", tool_name, skill_name)

        logger.debug("DynamicSkillToolSet: Returning %s tools from %s skill(s): %s", len(selected_tools),
                     len(skills_to_process), list(selected_tool_names))
        logger.debug("DynamicSkillToolSet: Returning %s tools from %s skill(s): %s", len(selected_tools),
                     len(skills_to_process), list(selected_tool_names))

        return selected_tools

    def _get_loaded_skills_from_state(self, ctx: InvocationContext) -> List[str]:
        """Get list of currently loaded skill names from session state.

        This returns ALL skills that are loaded in the session (both from previous
        turns and current turn).

        Args:
            ctx: Invocation context

        Returns:
            List of loaded skill names
        """
        loaded_skills: List[str] = []
        # Combine session state and current state delta
        state = dict(ctx.session_state.copy())
        state.update(ctx.actions.state_delta)

        for key, value in state.items():
            if key.startswith(SKILL_LOADED_STATE_KEY_PREFIX) and value:
                skill_name = key[len(SKILL_LOADED_STATE_KEY_PREFIX):]
                loaded_skills.append(skill_name)
        return loaded_skills

    def _get_active_skills_from_delta(self, ctx: InvocationContext) -> List[str]:
        """Get list of skills that are active in the current turn.

        A skill is considered "active" if:
        1. It was just loaded (skill:loaded: in state_delta), OR
        2. Its tools were just selected/modified (skill:tools: in state_delta)

        This is useful for only returning tools relevant to the current conversation
        context, rather than all tools from all loaded skills.

        Args:
            ctx: Invocation context

        Returns:
            List of active skill names in current turn
        """
        active_skills: set[str] = set()

        # Check state_delta for skill-related changes
        for key, value in ctx.actions.state_delta.items():
            if key.startswith(SKILL_LOADED_STATE_KEY_PREFIX) and value:
                # Skill was just loaded
                skill_name = key[len(SKILL_LOADED_STATE_KEY_PREFIX):]
                active_skills.add(skill_name)
                logger.debug("Skill '%s' is active (just loaded)", skill_name)
                logger.debug("Skill '%s' is active (just loaded)", skill_name)
            elif key.startswith(SKILL_TOOLS_STATE_KEY_PREFIX):
                # Skill's tools were just selected/modified
                skill_name = key[len(SKILL_TOOLS_STATE_KEY_PREFIX):]
                active_skills.add(skill_name)
                logger.debug("Skill '%s' is active (tools modified)", skill_name)
                logger.debug("Skill '%s' is active (tools modified)", skill_name)

        return list(active_skills)

    def _get_tools_selection(self, ctx: InvocationContext, skill_name: str) -> list[str]:
        """Get the list of selected tools for a skill from session state.

        Args:
            ctx: Invocation context
            skill_name: Skill name

        Returns:
            List of selected tool names
        """
        key = SKILL_TOOLS_STATE_KEY_PREFIX + skill_name
        v = get_state_delta(ctx, key)
        if not v:
            # Fallback to SKILL.md defaults when explicit selection state is absent.
            return self._get_skill_default_tools(skill_name)

        v_str = v.decode('utf-8') if isinstance(v, bytes) else str(v)

        if v_str == "*":
            # Select all tools defined in the skill
            return self._get_skill_default_tools(skill_name)

        try:
            arr = json.loads(v_str)
            if isinstance(arr, list):
                return arr
            return self._get_skill_default_tools(skill_name)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tools selection for skill '%s': %s", skill_name, v_str)
            logger.warning("Failed to parse tools selection for skill '%s': %s", skill_name, v_str)
            return self._get_skill_default_tools(skill_name)

    def _get_skill_default_tools(self, skill_name: str) -> list[str]:
        """Return default tools from SKILL.md for a loaded skill."""
        try:
            sk = self._skill_repository.get(skill_name)
            if sk is None:
                return []
            return list(sk.tools or [])
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to get default tools for skill '%s': %s", skill_name, ex)
            logger.warning("Failed to get default tools for skill '%s': %s", skill_name, ex)
            return []
