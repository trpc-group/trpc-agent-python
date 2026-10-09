# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Build the skill toolset used by the example."""

import os
from pathlib import Path

from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import ENV_SKILLS_ROOT
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills.tools import LinkSkillStager


def _get_skill_paths() -> str:
    """Get the skill paths."""
    skills_root = os.getenv(ENV_SKILLS_ROOT)
    if skills_root:
        return skills_root
    current_path = Path(__file__).parent
    return str(current_path.parent / "skills")


def create_skill_tool_set():
    """Create a local skill repository with interactive execution enabled."""
    workspace_runtime = create_local_workspace_runtime()
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(
        skill_paths,
        workspace_runtime=workspace_runtime,
        use_cached_repository=True,
    )
    skill_toolset = SkillToolSet(
        repository=repository,
        skill_stager=LinkSkillStager(),
    )
    return skill_toolset, repository
