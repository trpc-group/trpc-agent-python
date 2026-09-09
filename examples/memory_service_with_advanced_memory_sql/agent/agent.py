"""Agent definition for the Advanced Memory SQL example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_report


def create_agent() -> LlmAgent:
    """Create an agent; Runner installs the Advanced Memory tools."""
    api_key, base_url, model_name = get_model_config()
    return LlmAgent(
        name="advanced_memory_sql_assistant",
        description="A minimal Advanced Memory SQL demonstration assistant",
        model=OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
        ),
        instruction=INSTRUCTION,
        tools=[FunctionTool(get_weather_report)],
    )
