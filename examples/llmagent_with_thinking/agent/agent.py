# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.planners import BuiltInPlanner
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import ThinkingConfig

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_forecast
from .tools import get_weather_report


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=url,
        # There are two scenarios where enabling add_tools_to_prompt can improve the generation performance of the Agent:
        # 1. When the thinking model does not support tool calling,
        #    you can enable the ToolPrompt framework to parse the tool calling capability from the LLM-generated text.
        # 2. When the thinking model calls tools during the reasoning process,
        #    if the LLM model service fails to return the JSON format of tool calls, you can also enable ToolPrompt.
        #    This will prompt the LLM model to output the special text for tool calling in the main content,
        #    thereby increasing the probability of successful tool invocation.
        # You can uncomment the code below to use ToolPrompt.
        # add_tools_to_prompt=True,
    )
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
        # Note: thinking_budget must be less than max_output_tokens
        generate_content_config=GenerateContentConfig(max_output_tokens=10240, ),
        # The model must be a thinking model to use this Planner; this configuration will not take effect for non-thinking models.
        planner=BuiltInPlanner(thinking_config=ThinkingConfig(
            include_thoughts=True,
            thinking_budget=2048,
        ), ),
    )


root_agent = create_agent()
