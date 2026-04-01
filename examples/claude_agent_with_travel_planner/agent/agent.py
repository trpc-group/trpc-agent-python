# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from claude_agent_sdk.types import ClaudeAgentOptions
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import DuckDuckGoSearchMCP
from .tools import get_current_date

CLAUDE_ALLOWED_TOOLS = ["TodoWrite"]


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> ClaudeAgent:
    """ Create an agent"""
    search_tools = DuckDuckGoSearchMCP()

    agent = ClaudeAgent(
        name="travel_planner",
        description="旅游规划助手",
        model=_create_model(),
        instruction=INSTRUCTION,
        claude_agent_options=ClaudeAgentOptions(allowed_tools=CLAUDE_ALLOWED_TOOLS, ),
        tools=[
            FunctionTool(get_current_date),
            search_tools,
        ],
    )
    return agent


def setup_claude(proxy_host: str = "0.0.0.0", proxy_port: int = 8082):
    """Setup Claude environment (proxy server)"""
    claude_default_model = _create_model()
    setup_claude_env(
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        claude_models={"all": claude_default_model},
    )


def cleanup_claude():
    """Clean up Claude environment (stop proxy server)"""
    destroy_claude_env()
