"""Code review agent definition — integrates with tRPC-Agent framework."""

import os

from trpc_agent_sdk.agents import LlmAgent

# Default model: use env var or fake mode
_DEFAULT_MODEL = os.environ.get("CR_AGENT_MODEL", "fake")
_DEFAULT_INSTRUCTION = """You are a code review agent. Your job is to:
1. Load the code-review skill to understand review rules.
2. Analyze the provided diff for issues.
3. Output structured findings in the review format.
"""


def create_code_review_agent(model_config: dict | None = None) -> LlmAgent:
    """Create a LlmAgent configured for code review tasks.

    The agent uses the code-review Skill for rule loading and
    structured output generation.

    Args:
        model_config: Optional model configuration dict.  Supports:
            - model: model name (defaults to CR_AGENT_MODEL env var or "fake")
            - instruction: custom system instruction
            - temperature: model temperature
            - Any other LlmAgent kwargs

    Returns:
        Configured LlmAgent instance.
    """
    config = dict(model_config) if model_config else {}

    model = config.pop("model", _DEFAULT_MODEL)
    instruction = config.pop("instruction", _DEFAULT_INSTRUCTION)

    agent = LlmAgent(
        name="code_review_agent",
        description="Automated code review agent that analyzes diffs "
                     "for security, quality, and best practice issues.",
        model=model,
        instruction=instruction,
        **config,
    )

    return agent
