# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Weather agent with AG-UI cancel support. """

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
    """Create a weather query agent for AG-UI service with cancel support.

    When the client closes the SSE connection, the AG-UI service detects the
    disconnect and automatically triggers cooperative cancellation. The agent
    stops at the next checkpoint and partial results are saved to the session.
    """
    weather_tool = FunctionTool(get_weather_report)

    return LlmAgent(
        name="weather_agent_with_cancel",
        description="A professional weather query assistant that supports cancellation via SSE disconnect.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool],
    )


root_agent = create_agent()
