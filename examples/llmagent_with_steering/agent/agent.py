# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

""" Weather agent used by the steering example. """

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .prompts import INSTRUCTION
from .tools import get_weather_report
from .config import get_model_config


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a weather query agent with a slow tool.

    The tool delay gives the caller enough time to send a steering message
    while the run is still active.
    """

    weather_tool = FunctionTool(get_weather_report)

    return LlmAgent(
        name="weather_agent_with_steering",
        description="A weather query assistant that accepts steering messages.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool],
    )


root_agent = create_agent()
