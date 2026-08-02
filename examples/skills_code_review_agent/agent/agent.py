"""Build the reasoning agent used by the review workflow."""

from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel

from reports.models import ReviewAnalysis
from filters.policy import ReviewPolicyContext
from sandbox.base import SandboxProvider

from .config import ModelConfig
from .prompts import INSTRUCTION
from .tools import create_skill_tools

OUTPUT_KEY = "review_analysis"


def create_review_agent(
    model_config: ModelConfig,
    sandbox: SandboxProvider,
    repository_path: Path,
    skills_path: Path,
    policy_context: ReviewPolicyContext,
) -> LlmAgent:
    """Create an LLM agent with Docker-backed Skill tools."""
    toolset, skill_repository, _runtime = create_skill_tools(
        sandbox,
        repository_path,
        skills_path,
        policy_context,
    )
    model = OpenAIModel(
        model_name=model_config.model_name,
        api_key=model_config.api_key,
        base_url=model_config.base_url,
    )
    return LlmAgent(
        name="code_review_agent",
        description="Reviews code by selecting and running sandboxed Agent Skills.",
        model=model,
        instruction=INSTRUCTION,
        tools=[toolset],
        skill_repository=skill_repository,
        output_schema=ReviewAnalysis,
        output_key=OUTPUT_KEY,
    )
