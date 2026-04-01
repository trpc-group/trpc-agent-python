# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""
List tools for a skill.
"""

from __future__ import annotations

import re
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger

from .._constants import SKILL_REPOSITORY_KEY
from .._repository import BaseSkillRepository


def _extract_shell_examples_from_skill_body(body: str, limit: int = 5) -> list[str]:
    """Extract likely runnable command examples from SKILL.md body."""
    if not body:
        return []
    out: list[str] = []
    seen: set[str] = set()
    lines = body.splitlines()

    def maybe_add(cmd: str) -> None:
        cmd = re.sub(r"\s+", " ", (cmd or "").strip())
        if not cmd or cmd in seen:
            return
        if not re.match(r"^[A-Za-z0-9_./$\"'`-]", cmd):
            return
        seen.add(cmd)
        out.append(cmd)

    i = 0
    while i < len(lines) and len(out) < limit:
        cur = lines[i].strip()
        if cur.lower() != "command:":
            i += 1
            continue
        i += 1
        block: list[str] = []
        while i < len(lines):
            raw = lines[i]
            s = raw.strip()
            if not s:
                if block:
                    break
                i += 1
                continue
            if s.lower() in ("command:", "output files", "overview", "examples", "tools:"):
                break
            if re.match(r"^\d+\)", s):
                break
            if raw.startswith(" ") or raw.startswith("\t"):
                block.append(s)
                i += 1
                continue
            if block:
                break
            i += 1
        if block:
            merged = " ".join(part.rstrip("\\").strip() for part in block)
            maybe_add(merged)
    return out


def skill_list_tools(tool_context: InvocationContext, skill_name: str) -> dict[str, Any]:
    """List executable guidance for a skill.

    Args:
        skill_name: The name of the skill to load.
    Returns:
        Object containing declared tools and command examples from SKILL.md.
    """
    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    skill = repository.get(skill_name)
    if skill is None:
        logger.error("Skill %s not found", repr(skill_name))
        return {"tools": [], "command_examples": []}
    return {
        "tools": list(skill.tools or []),
        "command_examples": _extract_shell_examples_from_skill_body(skill.body, limit=5),
    }
