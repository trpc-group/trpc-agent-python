# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools for the agent.

Demonstrates how to wrap an LlmAgent as a tool using AgentTool,
enabling inter-agent collaboration.
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.tools import AgentTool

from .prompts import TRANSLATOR_INSTRUCTION


def create_translator_tool(model: LLMModel) -> AgentTool:
    """Create a translator agent and wrap it as an AgentTool.

    Args:
        model: The LLM model instance shared across agents.

    Returns:
        An AgentTool wrapping the translator agent.
    """
    translator = LlmAgent(
        name="translator",
        model=model,
        description="A professional text translation tool",
        instruction=TRANSLATOR_INSTRUCTION,
    )
    return AgentTool(agent=translator)
