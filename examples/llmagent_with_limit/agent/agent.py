# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent used by the run-limit example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_forecast
from .tools import get_weather_report


def _create_model() -> LLMModel:
    """Create the model configured for this example."""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
    )


def create_agent() -> LlmAgent:
    """Create the weather Agent used to trigger the run limits."""
    return LlmAgent(
        name="weather_agent",
        description="A weather assistant used to demonstrate run limits.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            FunctionTool(get_weather_report),
            FunctionTool(get_weather_forecast),
        ],
    )


root_agent = create_agent()
