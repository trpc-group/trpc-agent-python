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
    """List callable tools declared for a skill.

    Args:
        skill_name: The name of the skill to load.
    Returns:
        Object containing available tools.
    """
    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    skill = repository.get(skill_name)
    if skill is None:
        logger.error("Skill %s not found", repr(skill_name))
        return {"available_tools": []}
    return {"available_tools": list(skill.tools or [])}
