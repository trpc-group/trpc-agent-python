"""Construct the single structured-output review agent."""

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel

from ..code_review.models import ReviewOutput
from .config import get_model_config
from .prompts import INSTRUCTION
from .skills import create_review_skill_toolset


def create_agent() -> LlmAgent:
    """Create a diff-only reviewer with schema-constrained output."""
    api_key, base_url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)
    skill_toolset, skill_repository = create_review_skill_toolset()
    return LlmAgent(
        name="code_reviewer",
        description="Reviews a bounded Git diff and returns structured findings.",
        model=model,
        instruction=INSTRUCTION,
        tools=[skill_toolset],
        skill_repository=skill_repository,
        output_schema=ReviewOutput,
        output_key="code_review_output",
        include_previous_history=False,
        max_history_messages=1,
    )
