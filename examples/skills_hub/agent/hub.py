# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill Hub glue: declare a remote skill and build a repository for it."""

from __future__ import annotations

import os
from pathlib import Path

from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills.hub import GitHubAuth
from trpc_agent_sdk.skills.hub import GitHubSource
from trpc_agent_sdk.skills.hub import SkillSpec
from trpc_agent_sdk.skills.hub import SkillSpecsConfig

# `skill-creator` is Anthropic's own skill for building and iterating on
# skills -- a fitting "meta" skill to fetch through the Skill Hub for a demo.
GITHUB_SKILL_IDENTIFIER = "anthropics/skills/skills/skill-creator"
GITHUB_SKILL_NAME = "skill-creator"


def create_skill_tool_set(skills_dir: Path) -> tuple[SkillToolSet, BaseSkillRepository]:
    """Build a `SkillToolSet` backed by a GitHub skill installed through the repository factory."""
    workspace_runtime = create_local_workspace_runtime()
    # Unauthenticated requests are capped at 60 req/hr, which is plenty for
    # this demo. Set GITHUB_TOKEN to raise that limit for repeated runs.
    source = GitHubSource(GitHubAuth(os.getenv("GITHUB_TOKEN") or None))
    repository = create_default_skill_repository(
        additional_skill_specs=SkillSpecsConfig(
            specs=[
                SkillSpec(
                    source=source,
                    identifier=GITHUB_SKILL_IDENTIFIER,
                    name=GITHUB_SKILL_NAME,
                    on_error="raise",
                ),
            ],
            install_path=str(skills_dir / ".downloaded"),
        ),
        workspace_runtime=workspace_runtime,
    )
    skill_toolset = SkillToolSet(repository=repository)
    return skill_toolset, repository
