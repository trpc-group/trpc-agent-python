# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TeamAgent setup with ClaudeAgent as a member."""

from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import LEADER_INSTRUCTION
from .prompts import WEATHER_EXPERT_INSTRUCTION
from .tools import get_weather


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_team():
    """Create a team with ClaudeAgent as a member.

    This system demonstrates TeamAgent with heterogeneous members:
    - Leader coordinates tasks using LlmAgent
    - Claude member executes weather queries using ClaudeAgent
    """

    model = _create_model()

    # Claude member agent - weather expert using Claude
    claude_weather_agent = ClaudeAgent(
        name="weather_expert",
        description="Weather expert powered by Claude, can query weather information",
        model=model,
        instruction=WEATHER_EXPERT_INSTRUCTION,
        tools=[FunctionTool(get_weather)],
    )
    # Initialize Claude agent
    claude_weather_agent.initialize()

    # Team leader using LlmAgent
    team = TeamAgent(
        name="assistant_team",
        model=model,
        members=[claude_weather_agent],
        instruction=LEADER_INSTRUCTION,
    )

    return team, claude_weather_agent


def setup_environment():
    """Setup Claude environment.

    Returns:
        The model used for Claude environment setup.
    """
    model = _create_model()
    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8083, claude_models={"all": model})
    return model


def cleanup_environment():
    """Cleanup Claude environment."""
    destroy_claude_env()
