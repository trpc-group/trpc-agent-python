"""Agent definition for the Advanced Memory example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION


def create_agent() -> LlmAgent:
    """Create an agent; Advanced Memory tools are installed by run_agent.py."""
    api_key, base_url, model_name = get_model_config()
    return LlmAgent(
        name="advanced_memory_assistant",
        description="A minimal Advanced Memory demonstration assistant",
        model=OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
        ),
        instruction=INSTRUCTION,
    )
