# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Construct the code-review LlmAgent (Skills + sandbox + storage), used by run_agent.py."""
from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent

from .config import get_model
from .config import get_runtime_kind
from .prompts import INSTRUCTION
from .tools import build_host_tools
from .tools import create_skill_tool_set


def create_agent(dry_run: bool = False, runtime: str | None = None) -> LlmAgent:
    """An LlmAgent that reviews a diff by loading and running the ``code-review`` Skill.

    The agent gets the framework's Skills toolset (``skill_load`` / ``skill_run`` / ``skill_list``
    ...) plus two host-side tools that stage the diff and finalize the report. ``skill_repository``
    is passed so the agent can resolve skills from the same repository the toolset uses.

    ``dry_run`` selects the fake model; ``runtime`` overrides the sandbox runtime (default
    ``container``, with ``local`` as the development fallback).
    """
    # A dry run is for exercising the chain without external dependencies, so it must not also
    # demand a Docker daemon and a built scanner image. An explicit --runtime still wins.
    resolved = runtime or ("local" if dry_run else get_runtime_kind())
    skill_toolset, repository = create_skill_tool_set(resolved)
    return LlmAgent(
        name="code_review_agent",
        description="An automated code reviewer that runs the code-review Skill in a sandbox.",
        model=get_model(force_fake=dry_run),
        instruction=INSTRUCTION,
        tools=[skill_toolset, *build_host_tools()],
        skill_repository=repository,
    )
