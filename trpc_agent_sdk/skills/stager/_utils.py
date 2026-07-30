# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill staging utilities."""

import posixpath

from trpc_agent_sdk.code_executors import DIR_SKILLS


def default_workspace_skill_dir(skill_name: str) -> str:
    """Return the default workspace-relative directory for *skill_name*.

    Mirrors Go's ``defaultWorkspaceSkillDir``.
    """
    return posixpath.join(DIR_SKILLS, skill_name)
