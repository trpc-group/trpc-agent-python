# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""List doc filenames for a skill.
"""

from __future__ import annotations

from typing import Optional

from trpc_agent_sdk.context import InvocationContext

from .._constants import SKILL_REPOSITORY_KEY
from .._repository import BaseSkillRepository


def skill_list_docs(tool_context: InvocationContext, skill_name: str) -> list[str]:
    """List doc filenames for a skill.

    Args:
        skill_name: The name of the skill.

    Returns:
        Array of doc filenames.
    """
    normalized_skill = (skill_name or "").strip()
    if not normalized_skill:
        raise ValueError("skill is required")

    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        return []

    try:
        skill = repository.get(normalized_skill)
    except ValueError as ex:
        raise ValueError(f"unknown skill: {normalized_skill}") from ex
    return [resource.path for resource in (skill.resources or [])]
