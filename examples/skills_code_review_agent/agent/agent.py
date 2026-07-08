"""Optional LlmAgent wrapper for the code-review skill.

The tested CLI in ``run_agent.py`` is deterministic and does not require model
credentials. This module mirrors the repository's agent examples for users who
want a normal LlmAgent that can call the bundled SkillToolSet.
"""

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import create_review_skill_tool_set


def _create_model() -> LLMModel:
    """Create a model from the standard example environment variables."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create an LlmAgent wired to the bundled code-review Skill."""
    skill_tool_set, skill_repository = create_review_skill_tool_set()
    return LlmAgent(
        name="skills_code_review_agent",
        description="Automatic code review agent using Skills, sandbox scripts and structured reports.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[skill_tool_set],
        skill_repository=skill_repository,
    )


root_agent = create_agent()
