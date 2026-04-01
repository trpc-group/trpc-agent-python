# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Weather agent using ClaudeAgent. """

from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather


def _create_model() -> OpenAIModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> ClaudeAgent:
    """Create a weather query agent.

    This agent demonstrates basic ClaudeAgent usage:
    - Custom model configuration
    - FunctionTool integration
    - Session management
    """
    weather_tool = FunctionTool(get_weather)

    return ClaudeAgent(
        name="claude_weather_agent",
        description="A helpful weather query assistant.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[weather_tool],
        enable_session=True,
    )


root_agent = create_agent()
