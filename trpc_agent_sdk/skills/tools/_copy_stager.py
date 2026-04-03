# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill staging interface and default copy-based implementation.

This module provides the CopySkillStager class which is responsible for staging
skills to the workspace.
"""

from __future__ import annotations

import posixpath

from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_RUNS
from trpc_agent_sdk.code_executors import DIR_SKILLS
from trpc_agent_sdk.code_executors import DIR_WORK

from ..stager import SkillStageRequest
from ..stager import SkillStageResult
from ..stager import Stager

# ---------------------------------------------------------------------------
# Error messages (mirrors Go const block in tool/skill/stager.go)
# ---------------------------------------------------------------------------

_ERR_STAGER_NOT_CONFIGURED = "skill stager is not configured"
_ERR_REPO_NOT_CONFIGURED = "skill repository is not configured"

# ---------------------------------------------------------------------------
# Allowed workspace roots (mirrors Go isAllowedWorkspacePath)
# ---------------------------------------------------------------------------

_ALLOWED_WS_ROOTS = (
    DIR_SKILLS,  # "skills"
    DIR_WORK,  # "work"
    DIR_OUT,  # "out"
    DIR_RUNS,  # "runs"
)

# ---------------------------------------------------------------------------
# Path normalization helpers
# (mirrors normalizeWorkspaceSkillDir / normalizeSkillStageResult)
# ---------------------------------------------------------------------------


def normalize_workspace_skill_dir(raw: str) -> str:
    """Normalize and validate a workspace-relative skill directory.

    Mirrors Go's ``normalizeWorkspaceSkillDir``.  Strips leading slashes,
    normalizes path separators, and ensures the result stays within a
    known workspace root.

    Raises:
        ValueError: When the path is empty or escapes the workspace.
    """
    d = raw.strip().replace("\\", "/")
    if not d:
        raise ValueError("workspace skill dir must not be empty")

    if d.startswith("/"):
        d = posixpath.normpath(d).lstrip("/") or "."

    d = posixpath.normpath(d)
    if not d or d == ".":
        return "."

    first = d.split("/")[0]
    if first not in _ALLOWED_WS_ROOTS:
        raise ValueError(f"workspace skill dir {raw!r} must stay within the workspace")
    return d


def _normalize_skill_stage_result(result: SkillStageResult) -> SkillStageResult:
    """Return *result* with :attr:`workspace_skill_dir` normalized.

    Mirrors Go's ``normalizeSkillStageResult``.

    Raises:
        ValueError: Propagated from :func:`normalize_workspace_skill_dir`.
    """
    return SkillStageResult(workspace_skill_dir=normalize_workspace_skill_dir(result.workspace_skill_dir))


# ---------------------------------------------------------------------------
# Default copy-based stager (mirrors Go copySkillStager)
# ---------------------------------------------------------------------------


class CopySkillStager(Stager):
    """Default stager: copies the skill directory into ``skills/<name>``.

    Mirrors Go's ``copySkillStager`` struct.  Holds an optional
    back-reference to the owning ``SkillRunTool`` (``run_tool``), matching
    the Go pattern of ``copySkillStager{tool: tool}``.

    The actual filesystem work — digest check, ``stage_directory``, symlink
    creation, read-only chmod — is delegated to
    :class:`~trpc_agent_sdk.skills.stager.Stager`, which mirrors
    :class:`~trpc_agent_sdk.skills.stager.Stager`.

    Construct via :func:`new_copy_skill_stager` or
    :class:`~trpc_agent_sdk.skills.tools.CopySkillStager`.
    """

    async def stage_skill(self, request: SkillStageRequest) -> SkillStageResult:
        """Stage the skill and return the normalized workspace skill dir.

        Mirrors Go's ``copySkillStager.StageSkill``:

        1. Validate repository is present.
        2. Resolve the on-disk skill root via the repository.
        3. Resolve the runtime (``request.engine`` takes priority, falls back
           to ``repository.workspace_runtime``).
        4. Delegate copy / link / chmod to :class:`~trpc_agent_sdk.skills.stager.Stager`.
        5. Return a normalized :class:`SkillStageResult`.
        """
        if request.repository is None:
            raise ValueError(_ERR_REPO_NOT_CONFIGURED)

        result = await super().stage_skill(request)

        return _normalize_skill_stage_result(result)
