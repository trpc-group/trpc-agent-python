# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Weather agent for A2A example"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_report


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a weather query agent for A2A deployment.

    This agent provides weather information for various cities.
    The agent is deployed via A2A protocol using standard HTTP.
    """

    weather_tool = FunctionTool(get_weather_report)

    return LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool],
    )


root_agent = create_agent()
