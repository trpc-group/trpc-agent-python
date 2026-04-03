# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill staging types."""

from dataclasses import dataclass
from typing import Optional

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.context import InvocationContext

from .._repository import BaseSkillRepository


@dataclass
class SkillStageRequest:
    """Describes the skill staging context for one run.

    Mirrors Go's ``SkillStageRequest`` in ``tool/skill/stager.go``.
    """

    skill_name: str
    """Name of the skill to stage."""

    repository: BaseSkillRepository
    """Skill repository used to look up the skill root path."""

    workspace: WorkspaceInfo
    """Target workspace."""

    ctx: InvocationContext
    """Invocation context (used for workspace FS/runner access)."""

    engine: Optional[BaseWorkspaceRuntime] = None
    """Explicit workspace runtime (Go: ``Engine``).
    When ``None`` the runtime is obtained from ``repository.workspace_runtime``.
    """

    timeout: float = 300.0
    """Timeout in seconds for internal staging helpers."""


@dataclass
class SkillStageResult:
    """Reports where the staged skill lives inside the workspace."""

    workspace_skill_dir: str
    """Workspace-relative path of the specific staged skill directory.

    For example ``"skills/weather"`` or ``"work/custom/weather"``.
    Always within a known workspace root; never a sandbox absolute path.
    """
