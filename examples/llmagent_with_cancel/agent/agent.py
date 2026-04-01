# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Weather agent with cancellation support. """

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
    """Create a weather query agent with cancellation support.

    This agent demonstrates cooperative cancellation at various checkpoints:
    - Before LLM calls
    - During LLM streaming
    - Before and during tool execution

    The tool has a 3-second delay to simulate a slow API call, which gives
    us enough time to cancel during execution.
    """

    # Create tool with simulated delay to demonstrate cancellation
    weather_tool = FunctionTool(get_weather_report)

    return LlmAgent(
        name="weather_agent_with_cancel",
        description="A professional weather query assistant that supports cancellation at any time.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool],
    )


root_agent = create_agent()
