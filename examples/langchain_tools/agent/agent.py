# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import tavily_search


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """ Create an agent with LangChain Tavily search tool"""
    agent = LlmAgent(
        name="langchain_tavily_agent",
        description="An assistant integrated with LangChain Tavily search tool",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(tavily_search)],
    )
    return agent


root_agent = create_agent()
