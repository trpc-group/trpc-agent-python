# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Agent module"""

from trpc_agent.agents import LlmAgent
from trpc_agent.models import LLMModel
from trpc_agent.models import OpenAIModel
from trpc_agent.tools import FunctionTool

from .prompts import INSTRUCTION
from .tools import simple_search
from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """ Create an agent"""
    agent = LlmAgent(
        name="rag_agent",
        description="A helpful assistant for conversation",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(simple_search)],
    )
    return agent


root_agent = create_agent()
