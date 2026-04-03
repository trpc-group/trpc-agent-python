# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import get_tool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import calculate
from .tools import get_postal_code
from .tools import get_weather


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create an agent with function tools.

    Tools are created in two ways:
      - FunctionTool wrapping: get_weather, calculate, get_postal_code
      - Registry lookup: get_session_info (registered via @register_tool)
    """
    weather_tool = FunctionTool(get_weather)
    calculate_tool = FunctionTool(calculate)
    postal_code_tool = FunctionTool(get_postal_code)
    session_tool = get_tool("get_session_info")

    return LlmAgent(
        name="function_tool_demo_agent",
        description="An assistant demonstrating FunctionTool usage",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool, calculate_tool, postal_code_tool, session_tool],
    )


root_agent = create_agent()
