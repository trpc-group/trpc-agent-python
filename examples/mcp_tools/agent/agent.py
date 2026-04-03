# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import StdioMCPToolset


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """ Create an agent with MCP tools"""
    # Uncomment ONE of the following lines to match the transport mode in mcp_server.py:
    mcp_toolset = StdioMCPToolset()
    # mcp_toolset = SseMCPToolset()
    # mcp_toolset = StreamableHttpMCPToolset()

    agent = LlmAgent(
        name="mcp_assistant",
        description="An assistant that uses MCP tools for weather and calculation",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[mcp_toolset],
    )
    return agent


root_agent = create_agent()
