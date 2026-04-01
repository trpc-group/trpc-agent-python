# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Skill staging utilities."""

import posixpath

from trpc_agent_sdk.code_executors import DIR_SKILLS


def default_workspace_skill_dir(skill_name: str) -> str:
    """Return the default workspace-relative directory for *skill_name*.

    Mirrors Go's ``defaultWorkspaceSkillDir``.
    """
    return posixpath.join(DIR_SKILLS, skill_name)
