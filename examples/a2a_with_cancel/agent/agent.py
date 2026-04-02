# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Weather agent with cancellation support for A2A deployment. """

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


def create_agent() -> LlmAgent:
    """Create a weather query agent with cancellation support for A2A deployment.

    The tool has a 2-second delay to simulate a slow API call, which gives
    the client enough time to cancel during execution.
    """
    weather_tool = FunctionTool(get_weather_report)

    return LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant that supports cancellation.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool],
    )


root_agent = create_agent()
