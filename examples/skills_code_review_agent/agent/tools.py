"""Skill repository helpers for integrating the review skill with LlmAgent."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trpc_agent_sdk.skills import SkillToolSet


def get_skill_root() -> Path:
    """Return the example skill root."""
    return Path(__file__).resolve().parents[1] / "skills"


def create_review_skill_tool_set() -> tuple["SkillToolSet", object]:
    """Create a SkillToolSet for the bundled code-review skill.

    The deterministic CLI uses the same skill files directly so it can run
    without model credentials. This helper is provided for users who want to
    mount the skill into a regular LlmAgent and let the model call skill tools.
    """
    from trpc_agent_sdk.code_executors import create_local_workspace_runtime
    from trpc_agent_sdk.skills import SkillToolSet
    from trpc_agent_sdk.skills import create_default_skill_repository

    workspace_runtime = create_local_workspace_runtime()
    repository = create_default_skill_repository(
        str(get_skill_root()),
        workspace_runtime=workspace_runtime,
        use_cached_repository=True,
    )
    tool_set = SkillToolSet(
        repository=repository,
        save_as_artifacts=True,
        omit_inline_content=False,
    )
    return tool_set, repository
