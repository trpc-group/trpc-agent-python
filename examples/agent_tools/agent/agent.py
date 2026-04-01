# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import MAIN_INSTRUCTION
from .tools import create_translator_tool


def _create_model() -> LLMModel:
    """Create a model"""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create a main agent with an AgentTool for translation.

    The translator agent is wrapped as an AgentTool so the main agent
    can delegate translation tasks through tool invocation.
    """
    model = _create_model()
    translator_tool = create_translator_tool(model)

    return LlmAgent(
        name="content_processor",
        description="A content processing assistant that can invoke translation tools",
        model=model,
        instruction=MAIN_INSTRUCTION,
        tools=[translator_tool],
    )


root_agent = create_agent()
