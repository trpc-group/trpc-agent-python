# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """
import os
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import ENV_SKILLS_ROOT
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository


def _get_skill_paths() -> str:
    """Get the skill paths."""
    skills_root = os.getenv(ENV_SKILLS_ROOT)
    if skills_root:
        return skills_root
    current_path = Path(__file__).parent
    return str(current_path.parent.parent / "skills")


def _create_workspace_runtime(workspace_runtime_type: str = "local", **kwargs: Any) -> BaseWorkspaceRuntime:
    """Create a new workspace runtime."""
    inputs_host = kwargs.pop("inputs_host", None)
    if workspace_runtime_type == "container":
        skill_paths = _get_skill_paths()
        dock_path = "/opt/trpc-agent/skills"
        host_config = {}
        specs = []
        skill_spec = f"{skill_paths}:{dock_path}:ro"
        specs.append(skill_spec)
        if inputs_host:
            inputs_path = "/opt/trpc-agent/inputs"
            inputs_spec = f"{inputs_host}:{inputs_path}:ro"
            specs.append(inputs_spec)
        host_config["Binds"] = specs
        kwargs["host_config"] = host_config
        kwargs["auto_inputs"] = True
        return create_container_workspace_runtime(**kwargs)
    else:
        if inputs_host:
            kwargs["inputs_host_base"] = inputs_host
        return create_local_workspace_runtime(**kwargs)


def create_skill_tool_set(workspace_runtime_type: str = "local") -> tuple[SkillToolSet, BaseSkillRepository]:
    """Create a new skill tool set."""
    tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }
    workspace_runtime_args = {}
    # workspace_runtime = _create_workspace_runtime(workspace_runtime_type="container", **workspace_runtime_args)
    workspace_runtime = _create_workspace_runtime(workspace_runtime_type=workspace_runtime_type,
                                                  **workspace_runtime_args)
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
    return SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs), repository
