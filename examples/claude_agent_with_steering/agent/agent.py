# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Weather agent that accepts steering while a tool is running. """

from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_enable_session
from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import get_weather_report


def _create_model() -> OpenAIModel:
    """Create the model used by the agent."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> ClaudeAgent:
    """Create a weather agent that consumes steering between tool calls."""
    return ClaudeAgent(
        name="claude_weather_agent_with_steering",
        description="A weather assistant that accepts steering messages while running.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(get_weather_report)],
        enable_session=get_enable_session(),
    )


root_agent = create_agent()
