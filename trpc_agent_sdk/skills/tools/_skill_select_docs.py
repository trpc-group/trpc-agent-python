# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Select docs for a skill. Use mode=add to append, replace to overwrite, or clear to remove.
"""

from __future__ import annotations

from typing import Literal
from typing import Optional

from pydantic import Field
from trpc_agent_sdk.context import InvocationContext

from .._common import BaseSelectionResult
from .._common import append_loaded_order_state_delta
from .._common import docs_state_key
from .._common import get_agent_name
from .._common import get_previous_selection_by_key
from .._common import normalize_selection_mode
from .._common import set_selection_state_delta_by_key
from .._constants import SKILL_REPOSITORY_KEY
from .._repository import BaseSkillRepository


class SkillSelectDocsResult(BaseSelectionResult):
    """Result for selecting docs of a skill."""
    selected_docs: list[str] = Field(default_factory=list, description="The selected docs of the skill.")
    include_all_docs: bool = Field(default=False, description="Whether to include all docs of the skill.")

    # Accept alias fields during initialization (excluded from serialization)
    selected_items: list[str] = Field(default=None, exclude=True, repr=False)
    include_all: bool = Field(default=None, exclude=True, repr=False)

    def model_post_init(self, __context) -> None:
        """Handle alias fields after model initialization."""
        # If selected_items was explicitly provided, use it to set selected_docs
        if self.selected_items is not None:
            self.selected_docs = self.selected_items
        # If include_all was explicitly provided, use it to set include_all_docs
        if self.include_all is not None:
            self.include_all_docs = self.include_all


def skill_select_docs(tool_context: InvocationContext,
                      skill_name: str,
                      docs: Optional[list[str]] = None,
                      include_all_docs: bool = False,
                      mode: Literal['add', 'replace', 'clear'] = "replace") -> SkillSelectDocsResult:
    """Select docs for a skill. Use mode=add to append, replace to overwrite, or clear to remove.
    Args:
        skill_name: The name of the skill to select the docs of.
        docs: The docs of the skill to select the docs of.
        include_all_docs: Whether to include all docs of the skill.
        mode: The mode to use for selecting the docs of the skill.

    Returns:
        A message indicating the docs were selected.
    """
    normalized_skill = (skill_name or "").strip()
    if not normalized_skill:
        raise ValueError("skill is required")
    normalized_mode = normalize_selection_mode(mode)
    agent_name = get_agent_name(tool_context)

    repository: Optional[BaseSkillRepository] = tool_context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)
    if repository is not None:
        try:
            _ = repository.get(normalized_skill)
        except ValueError as ex:
            raise ValueError(f"unknown skill: {normalized_skill}") from ex

    docs_selection_key = docs_state_key(tool_context, normalized_skill)
    previous_items, had_all = get_previous_selection_by_key(tool_context, docs_selection_key)
    if had_all and normalized_mode != "clear":
        result = SkillSelectDocsResult(
            skill=normalized_skill,
            selected_items=[],
            include_all=True,
            mode=normalized_mode,
        )
    elif normalized_mode == "clear":
        result = SkillSelectDocsResult(
            skill=normalized_skill,
            selected_items=[],
            include_all=False,
            mode="clear",
        )
    elif normalized_mode == "add":
        selected = set(previous_items)
        for item in docs or []:
            selected.add(item)
        result = SkillSelectDocsResult(
            skill=normalized_skill,
            selected_items=[] if include_all_docs else list(selected),
            include_all=include_all_docs,
            mode="add",
        )
    else:
        result = SkillSelectDocsResult(
            skill=normalized_skill,
            selected_items=[] if include_all_docs else list(docs or []),
            include_all=include_all_docs,
            mode="replace",
        )

    set_selection_state_delta_by_key(
        tool_context,
        docs_selection_key,
        result.selected_docs,
        result.include_all_docs,
    )
    append_loaded_order_state_delta(tool_context, agent_name, normalized_skill)
    return result
