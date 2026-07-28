"""Optional LLM agent assembly."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.skills import SkillToolSet, create_default_skill_repository


def create_review_agent(runtime: Any, model: Any, review_filter: Any, *, production: bool = True) -> LlmAgent:
    if runtime is None:
        raise ValueError("a workspace runtime is required")
    if model is None:
        raise ValueError("a model is required; use --fake-model for deterministic review")
    runtime_name = type(runtime).__name__.lower()
    if production and "local" in runtime_name:
        raise ValueError("local runtime is an explicit development fallback and is not allowed in production")
    skills_path = str(__import__("pathlib").Path(__file__).parents[1] / "skills")
    skill_repository = create_default_skill_repository(skills_path, workspace_runtime=runtime)
    toolset = SkillToolSet(repository=skill_repository)
    return LlmAgent(
        name="code_review_agent",
        description="Policy-governed automatic code reviewer.",
        instruction=(
            "Review only added lines. Use the code-review skill, cite concrete evidence, "
            "and never execute a command that the review policy has not allowed."
        ),
        model=model,
        tools=[toolset],
    )
