# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent configured for the interactive skill execution example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import create_skill_tool_set


def _create_model() -> LLMModel:
    """Create the configured model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """Create an agent with the skill execution toolset."""
    skill_tool_set, skill_repository = create_skill_tool_set()

    return LlmAgent(
        name="skill_exec_agent",
        description="An assistant demonstrating interactive Agent Skill execution.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[skill_tool_set],
        skill_repository=skill_repository,
    )


root_agent = create_agent()
