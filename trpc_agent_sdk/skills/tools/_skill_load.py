# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Load a skill body and optional docs. Safe to call multiple times to add or replace docs.
Do not call this to list skills; names and descriptions are already in context.
Use when a task needs a skill's SKILL.md body and selected docs in context.
"""

from __future__ import annotations

import json
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext

from .._constants import SKILL_DOCS_STATE_KEY_PREFIX
from .._constants import SKILL_LOADED_STATE_KEY_PREFIX
from .._constants import SKILL_REPOSITORY_KEY
from .._constants import SKILL_TOOLS_STATE_KEY_PREFIX
from .._repository import BaseSkillRepository


def _set_state_delta(invocation_context: InvocationContext, key: str, value: Any) -> None:
    """Set the state delta of a skill loaded."""
    invocation_context.actions.state_delta[key] = value


def _set_state_delta_for_skill_load(invocation_context: InvocationContext,
                                    skill_name: str,
                                    docs: list[str],
                                    include_all_docs: bool = False) -> None:
    """Set the state delta of a skill loaded."""
    key = f"{SKILL_LOADED_STATE_KEY_PREFIX}{skill_name}"
    _set_state_delta(invocation_context, key, True)
    key = f"{SKILL_DOCS_STATE_KEY_PREFIX}{skill_name}"
    if include_all_docs:
        _set_state_delta(invocation_context, key, '*')
    else:
        _set_state_delta(invocation_context, key, json.dumps(docs or []))


def _set_state_delta_for_skill_tools(invocation_context: InvocationContext, skill_name: str, tools: list[str]) -> None:
    """Set the state delta of a skill tools."""
    key = f"{SKILL_TOOLS_STATE_KEY_PREFIX}{skill_name}"
    _set_state_delta(invocation_context, key, json.dumps(tools or []))


def skill_load(tool_context: InvocationContext,
               skill_name: str,
               docs: Optional[list[str]] = None,
               include_all_docs: bool = False) -> str:
    """Load a skill body and optional docs. Safe to call multiple times to add or replace docs.
    Do not call this to list skills; names and descriptions are already in context.
    Use when a task needs a skill's SKILL.md body and selected docs in context.
    Args:
        skill_name: The name of the skill to load.
        docs: The docs of the skill to load.
        include_all_docs: Whether to include all docs of the skill.

    Returns:
        A message indicating the skill was loaded.
    """

    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is None:
        raise ValueError("repository not found")
    skill = repository.get(skill_name)
    if skill is None:
        return f"skill {skill_name!r} not found"
    _set_state_delta_for_skill_load(tool_context, skill_name, docs or [], include_all_docs)
    if skill.tools:
        _set_state_delta_for_skill_tools(tool_context, skill_name, skill.tools)
    return f"skill {skill_name!r} loaded"
