# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools and skill setup for the team_with_skill example."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import ENV_SKILLS_ROOT
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository


async def search_web(query: str) -> str:
    """Search the web for information on a given topic (simulated)."""
    search_results = {
        "renewable energy": ("Research findings: Renewable energy deployment keeps growing, "
                             "with solar and wind as key drivers and lower generation costs."),
        "ai": ("Research findings: AI trends focus on multimodal models, "
               "agent systems, and enterprise automation."),
    }
    query_lower = query.lower()
    for key, value in search_results.items():
        if key in query_lower:
            return value
    return f"Research findings for '{query}': market and technology updates continue to evolve rapidly."


async def check_grammar(text: str) -> str:
    """Check the grammar and style of the given text."""
    word_count = len(text.split())
    return f"Grammar check completed: {word_count} words, readability looks good."


async def get_current_date() -> str:
    """Get current date and time in ISO format."""
    return datetime.now().isoformat()


def _get_skill_paths() -> str:
    """Get the skill root path from env or local example directory."""
    skills_root = os.getenv(ENV_SKILLS_ROOT)
    if skills_root:
        return skills_root
    current_path = Path(__file__).parent
    return str(current_path.parent / "skills")


def _create_workspace_runtime(
    workspace_runtime_type: str = "local",
    **kwargs: Any,
) -> BaseWorkspaceRuntime:
    """Create workspace runtime for skill execution."""
    inputs_host = kwargs.pop("inputs_host", None)
    if workspace_runtime_type == "container":
        skill_paths = _get_skill_paths()
        container_skill_path = "/opt/trpc-agent/skills"
        host_config: dict[str, list[str]] = {}
        bind_specs: list[str] = [f"{skill_paths}:{container_skill_path}:ro"]
        if inputs_host:
            container_inputs_path = "/opt/trpc-agent/inputs"
            bind_specs.append(f"{inputs_host}:{container_inputs_path}:ro")
        host_config["Binds"] = bind_specs
        kwargs["host_config"] = host_config
        kwargs["auto_inputs"] = True
        return create_container_workspace_runtime(**kwargs)

    if inputs_host:
        kwargs["inputs_host_base"] = inputs_host
    return create_local_workspace_runtime(**kwargs)


def create_skill_tool_set(workspace_runtime_type: str = "local", ) -> tuple[SkillToolSet, BaseSkillRepository]:
    """Create skill toolset and repository."""
    run_tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }
    workspace_runtime = _create_workspace_runtime(workspace_runtime_type=workspace_runtime_type)
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
    return SkillToolSet(repository=repository, run_tool_kwargs=run_tool_kwargs), repository
