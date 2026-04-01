# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """
import os
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import ENV_SKILLS_ROOT
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository


def _get_skill_paths() -> str:
    """Get the skill paths."""
    skills_root = os.getenv(ENV_SKILLS_ROOT)
    if skills_root:
        return skills_root
    current_path = Path(__file__).parent
    path = str(current_path.parent / "skills")
    # convert to file URL
    # path = "file://" + path
    # "http://{host}:{port}/{path}/{filename}.{extension}"
    # path = "http://localhost:8000/skills/skills.tar.gz"
    return path


def _create_workspace_runtime(**kwargs: Any) -> BaseWorkspaceRuntime:
    """Create a new workspace runtime."""
    inputs_host = kwargs.pop("inputs_host", None)
    if inputs_host:
        kwargs["inputs_host_base"] = inputs_host
    return create_local_workspace_runtime(**kwargs)


def create_skill_tool_set() -> SkillToolSet:
    """Create a new skill tool set."""
    tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }
    workspace_runtime_args = {}
    workspace_runtime = _create_workspace_runtime(**workspace_runtime_args)
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
    return SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs), repository
