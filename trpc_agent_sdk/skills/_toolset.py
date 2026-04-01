# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Skill toolset for integrating skills into the agent tool system.

This module provides the SkillToolSet class which makes skills available
as tools to agents.
"""

from __future__ import annotations

from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Union
from typing_extensions import override

from trpc_agent_sdk.abc import ToolABC
from trpc_agent_sdk.abc import ToolPredicate
from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import FunctionTool

from ._constants import SKILL_REGISTRY_KEY
from ._constants import SKILL_REPOSITORY_KEY
from ._registry import SKILL_REGISTRY
from ._registry import SkillToolFunction
from ._repository import BaseSkillRepository
from ._repository import FsSkillRepository
from ._run_tool import SkillRunTool
from ._tools import skill_list
from ._tools import skill_list_docs
from ._tools import skill_list_tools
from ._tools import skill_load
from ._tools import skill_select_docs
from ._tools import skill_select_tools


class SkillToolSet(ToolSetABC):
    """Toolset that provides tools from registered skills.

    This toolset integrates skills into the agent's tool system by exposing
    all tools from registered skills as available tools.

    Example:
        >>> from trpc_agent_sdk.skills import SkillRegistry, SkillToolSet
        >>> registry = SkillRegistry()
        >>> # Register skills...
        >>> toolset = SkillToolSet()
        >>> tools = await toolset.get_tools()
    """

    def __init__(self,
                 paths: Optional[List[str]] = None,
                 repository: BaseSkillRepository = None,
                 tool_filter: Optional[Union[ToolPredicate, List[str]]] = None,
                 is_include_all_tools: bool = True,
                 **run_tool_kwargs: dict[str, Any]):
        """Initialize the skill toolset.

        Args:
            paths: Optional list of skill paths. If None, will create a new one.
            repository: Skill repository. If None, will be retrieved from context metadata.
            tool_filter: Optional tool filter. If None, will include all tools.
            is_include_all_tools: Optional flag to include all tools. If True, will include all tools.
            run_tool_kwargs: Optional keyword arguments for skill run tool. If None, will use default values.
        """
        super().__init__(tool_filter=tool_filter, is_include_all_tools=is_include_all_tools)
        self.name = "skill_toolset"
        self._repository = repository or FsSkillRepository(*(paths or []))
        self._run_tool = SkillRunTool(repository=self._repository, **run_tool_kwargs)
        self._tools: List[Callable] = [
            skill_load,
            skill_list,
            skill_list_docs,
            skill_list_tools,
            skill_select_docs,
            skill_select_tools,
        ]

    @property
    def repository(self) -> BaseSkillRepository:
        """Get the skill repository."""
        return self._repository

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[ToolABC]:
        """Get all tools from registered skills.

        Args:
            invocation_context: Optional invocation context (not used currently)

        Returns:
            List of tools from all registered skills
        """
        tools: List[FunctionTool] = []
        skill_functions: List[SkillToolFunction] = SKILL_REGISTRY.get_all()
        skill_functions.extend(self._tools)
        agent_context = invocation_context.agent_context
        agent_context.with_metadata(SKILL_REGISTRY_KEY, SKILL_REGISTRY)
        agent_context.with_metadata(SKILL_REPOSITORY_KEY, self._repository)
        tools.append(self._run_tool)
        for skill_function in skill_functions:
            try:
                tools.append(FunctionTool(func=skill_function))
            except Exception as ex:  # pylint: disable=broad-except
                # Log error but continue loading other tools
                logger.warning("Failed to get tools from skill '%s': %s", skill_function.__name__, ex)
                continue

        return tools
