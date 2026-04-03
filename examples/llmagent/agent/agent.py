# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_forecast
from .tools import get_weather_report


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a weather query agent to demonstrate the various capabilities of an LLM agent."""

    # Create tools
    weather_tool = FunctionTool(get_weather_report)
    forecast_tool = FunctionTool(get_weather_forecast)

    return LlmAgent(
        name="weather_agent",
        description=
        "A professional weather query assistant that can provide real-time weather and forecast information.",
        model=_create_model(),
        # Use state variables for template replacement - Demonstration of the {var} syntax
        instruction=INSTRUCTION,
        tools=[weather_tool, forecast_tool],
        # Configure Generation Parameters
        generate_content_config=GenerateContentConfig(
            temperature=0.3,  # Reduce randomness for more deterministic responses
            top_p=0.9,
            max_output_tokens=1500,
        ),
        # Enable Planner to Enhance Reasoning Capabilities (Commented Out by Default)
        # Uncomment the line below to equip the model with reasoning capabilities,
        # allowing it to perform inference before generating responses
        # planner=PlanReActPlanner(),
    )


root_agent = create_agent()
