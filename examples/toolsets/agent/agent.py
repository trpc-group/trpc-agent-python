# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import WeatherToolSet


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """Create an agent with weather tool set"""
    weather_toolset = WeatherToolSet()
    weather_toolset.initialize()

    agent = LlmAgent(
        name="weather_toolset_agent",
        description="A weather assistant demonstrating ToolSet usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_toolset],
    )
    return agent


root_agent = create_agent()
