# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Weather agent: current weather, forecast, AQI, UV index."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_air_quality
from .tools import get_uv_index
from .tools import get_weather
from .tools import get_weather_forecast


def _create_model() -> OpenAIModel:
    """Create the model"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create the weather agent."""
    return LlmAgent(
        name="weather_agent",
        description="Weather query assistant, can query current weather, forecast, air quality, UV index",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            FunctionTool(get_weather),
            FunctionTool(get_weather_forecast),
            FunctionTool(get_air_quality),
            FunctionTool(get_uv_index),
        ],
    )


root_agent = create_agent()
