# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill toolset for integrating skills into the agent tool system.

This module provides the SkillToolSet class which makes skills available
as tools to agents.
"""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Optional
from typing import Union
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.abc import ToolPredicate
from trpc_agent_sdk.abc import ToolABC
from trpc_agent_sdk.code_executors import WorkspaceRuntimeResolver
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.log import logger

from ._constants import SKILL_REGISTRY_KEY
from ._constants import SKILL_REPOSITORY_KEY
from ._repository import FsSkillRepository
from ._repository import BaseSkillRepository
from ._registry import SKILL_REGISTRY
from ._repository import SkillRepositoryResolver
from ._registry import SkillToolFunction
from ._skill_config import DEFAULT_SKILL_CONFIG
from ._skill_config import set_skill_config
from ._skill_config import is_exist_skill_config
from .tools import skill_list_docs
from .tools import skill_list_tools
from .tools import SkillLoadTool
from .tools import skill_select_docs
from .tools import skill_select_tools
from .tools import skill_list
from .tools import SkillExecTool
from .tools import SkillRunTool
from .tools import SaveArtifactTool
from .tools import WorkspaceExecTool
from .tools import WorkspaceWriteStdinTool
from .tools import WorkspaceKillSessionTool
from .tools import CreateWorkspaceNameCallback
from .tools import default_create_ws_name_callback
from .tools import CopySkillStager
from .stager import Stager


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
                 repo_resolver: Optional[SkillRepositoryResolver] = None,
                 workspace_runtime_resolver: Optional[WorkspaceRuntimeResolver] = None,
                 enable_hot_reload: bool = False,
                 tool_filter: Optional[Union[ToolPredicate, List[str]]] = None,
                 is_include_all_tools: bool = True,
                 create_ws_name_cb: Optional[CreateWorkspaceNameCallback] = None,
                 runtime_tools: Optional[List[ToolABC]] = None,
                 skill_stager: Optional[Stager] = None,
                 skill_config: Optional[dict[str, Any]] = None,
                 **run_tool_kwargs: dict[str, Any]):
        """Initialize the skill toolset.

        Args:
            paths: Optional list of skill paths. If None, will create a new one.
            repository: Skill repository. If None, will be retrieved from context metadata.
            enable_hot_reload: Whether to enable skill hot reload checks for
                auto-created repositories.
            tool_filter: Optional tool filter. If None, will include all tools.
            is_include_all_tools: Optional flag to include all tools. If True, will include all tools.
            user_tools: Optional list of user tools. If None, will not include any user tools.
            run_tool_kwargs: Optional keyword arguments for skill run tool. If None, will use default values.
        """
        super().__init__(tool_filter=tool_filter, is_include_all_tools=is_include_all_tools)
        self.name = "skill_toolset"
        self._repo_resolver: Optional[SkillRepositoryResolver] = repo_resolver
        self._workspace_runtime_resolver: Optional[WorkspaceRuntimeResolver] = workspace_runtime_resolver
        self._repository = repository or FsSkillRepository(
            *(paths or []),
            enable_hot_reload=enable_hot_reload,
        )
        self._skill_config = skill_config or DEFAULT_SKILL_CONFIG
        self._create_ws_name_cb = create_ws_name_cb or default_create_ws_name_callback
        self._skill_stager = skill_stager or CopySkillStager()
        self._load_tool = SkillLoadTool(repository=self._repository,
                                        repo_resolver=repo_resolver,
                                        skill_stager=self._skill_stager,
                                        create_ws_name_cb=self._create_ws_name_cb)
        self._run_tool = SkillRunTool(repository=self._repository,
                                      repo_resolver=repo_resolver,
                                      create_ws_name_cb=self._create_ws_name_cb,
                                      skill_stager=self._skill_stager,
                                      **run_tool_kwargs)
        self._exec_tool = SkillExecTool(run_tool=self._run_tool, create_ws_name_cb=self._create_ws_name_cb)
        self._function_tools: List[SkillToolFunction] = [
            skill_list,
            skill_list_docs,
            skill_list_tools,
            skill_select_docs,
            skill_select_tools,
        ]
        if runtime_tools:
            self._runtime_tools = runtime_tools
        else:
            workspace_exec_tool = WorkspaceExecTool(workspace_runtime=self._repository.workspace_runtime,
                                                    workspace_runtime_resolver=self._workspace_runtime_resolver,
                                                    create_ws_name_cb=self._create_ws_name_cb)
            self._runtime_tools: List[ToolABC] = [
                SaveArtifactTool(workspace_runtime=self._repository.workspace_runtime,
                                 workspace_runtime_resolver=self._workspace_runtime_resolver,
                                 create_ws_name_cb=self._create_ws_name_cb),
                workspace_exec_tool,
                WorkspaceWriteStdinTool(workspace_exec_tool),
                WorkspaceKillSessionTool(workspace_exec_tool),
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
        tools: List[ToolABC] = []
        skill_functions: List[SkillToolFunction] = SKILL_REGISTRY.get_all()
        skill_functions.extend(self._function_tools)
        if self._repo_resolver is not None:
            repository = self._repo_resolver(invocation_context)
        else:
            repository = self._repository
        if not invocation_context:
            invocation_context = get_invocation_ctx()
        if invocation_context:
            agent_context = invocation_context.agent_context
            agent_context.with_metadata(SKILL_REGISTRY_KEY, SKILL_REGISTRY)
            agent_context.with_metadata(SKILL_REPOSITORY_KEY, repository)
            if not is_exist_skill_config(agent_context):
                set_skill_config(agent_context, self._skill_config)
        tools.append(self._load_tool)
        tools.append(self._run_tool)
        tools.append(self._exec_tool)
        tools.extend(self._runtime_tools)
        for skill_function in skill_functions:
            try:
                tools.append(FunctionTool(func=skill_function))
            except Exception as ex:  # pylint: disable=broad-except
                # Log error but continue loading other tools
                logger.warning("Failed to get tools from skill '%s': %s", skill_function.__name__, ex)
                continue

        return tools
