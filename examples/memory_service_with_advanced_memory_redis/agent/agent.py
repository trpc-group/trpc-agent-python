"""Agent definition for the Redis Advanced Memory example."""

import os

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .tools import get_weather_report

def create_agent() -> LlmAgent:
    """Create an agent whose Runner installs Advanced Memory tools."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise ValueError("TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL, and TRPC_AGENT_MODEL_NAME must be set")
    return LlmAgent(
        name="advanced_memory_redis_assistant",
        description="A Redis-backed Advanced Memory demonstration assistant",
        model=OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url),
        instruction=(
            "When the user asks you to remember a durable personal preference or fact, use save_memory. "
            "When the user asks what you remember, use list_memory_index first and read_memory for the "
            "relevant file. Always answer using the tool result."
        ),
        tools=[FunctionTool(get_weather_report)],
    )
