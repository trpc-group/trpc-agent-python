"""Code review agent definition — integrates with tRPC-Agent framework."""

from trpc_agent_sdk.agents import LlmAgent


def create_code_review_agent(model_config: dict | None = None) -> LlmAgent:
    """Create a LlmAgent configured for code review tasks.

    The agent uses the code-review Skill for rule loading and
    structured output generation.

    Args:
        model_config: Optional model configuration dict.

    Returns:
        Configured LlmAgent instance.
    """
    config = model_config or {
        "model": "fake",  # Default to fake mode
    }

    agent = LlmAgent(
        name="code_review_agent",
        description="Automated code review agent that analyzes diffs "
                     "for security, quality, and best practice issues.",
        model=config["model"],
        instruction="""You are a code review agent. Your job is to:
1. Load the code-review skill to understand review rules.
2. Analyze the provided diff for issues.
3. Output structured findings in the review format.
""",
    )

    return agent
