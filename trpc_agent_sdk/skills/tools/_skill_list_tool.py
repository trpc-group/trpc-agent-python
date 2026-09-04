# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
List tools for a skill.
"""

from __future__ import annotations

from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from .._constants import SKILL_REPOSITORY_KEY
from .._repository import BaseSkillRepository


def skill_list_tools(tool_context: InvocationContext, skill_name: str) -> dict[str, Any]:
    """List tool names declared by a specific skill.

    This only reports tools referenced by the selected skill. It does not list
    every tool available to the agent. An empty result means that this skill
    declares no tools; it does not mean that the agent has no tools available.

    Args:
        skill_name: The name of the skill to inspect.

    Returns:
        Object containing the tool names declared by this skill.
    """
    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    skill = repository.get(skill_name)
    if skill is None:
        logger.error("Skill %s not found", repr(skill_name))
        available_tools = []
    else:
        available_tools = list(skill.tools or [])
    return {
        "skill_name":
        skill_name,
        "available_tools":
        available_tools,
        "scope":
        "skill_declared_tools_only",
        "note":
        "Only tools declared by this skill are listed. "
        "This does not represent all tools available to the agent.",
    }
