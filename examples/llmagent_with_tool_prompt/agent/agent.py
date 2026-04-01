# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

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
        add_tools_to_prompt=True,
        # The framework provides two methods for injecting the tool_prompt: XML and JSON.
        # If the tool_prompt is not specified, the XML format will be used by default.
        # tool_prompt="xml",
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
    )


root_agent = create_agent()
