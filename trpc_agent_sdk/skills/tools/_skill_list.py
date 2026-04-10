# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""List all discovered skills, including disabled ones with their eligibility status.
"""

from __future__ import annotations

from typing import Literal
from typing import Optional

from trpc_agent_sdk.context import InvocationContext

from .._constants import SKILL_REPOSITORY_KEY
from .._repository import BaseSkillRepository


def skill_list(tool_context: InvocationContext, mode: Literal['all', 'available'] = 'all') -> list[str]:
    """List all discovered skills.

    Args:
        tool_context: The tool context.
        mode: The mode to list skills.
    Returns:
        A list of skill names.
    """
    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    return repository.skill_list(mode)
