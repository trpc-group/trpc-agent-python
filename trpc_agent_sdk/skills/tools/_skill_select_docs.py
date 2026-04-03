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
from .._common import generic_select_items
from .._constants import SKILL_DOCS_STATE_KEY_PREFIX


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
    result = generic_select_items(tool_context=tool_context,
                                  skill_name=skill_name,
                                  items=docs,
                                  include_all=include_all_docs,
                                  mode=mode,
                                  state_key_prefix=SKILL_DOCS_STATE_KEY_PREFIX,
                                  result_class=SkillSelectDocsResult)
    return result
