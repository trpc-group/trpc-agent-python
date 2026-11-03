# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Real LlmAgent wiring for the tool-safety review demo."""

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import ToolSearchOptions

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import create_tool_search_toolset


def _create_model() -> LLMModel:
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def create_agent(options: ToolSearchOptions | None = None) -> LlmAgent:
    """ Create an agent"""
    agent = LlmAgent(
        name="tool_search_agent",
        description="A tool search agent",
        model=_create_model(),  # You can change this to your preferred model
        instruction=INSTRUCTION,
        tools=[create_tool_search_toolset(options)],
    )
    return agent
