# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(search_tool, name: str = "rag_agent") -> LlmAgent:
    """ Create an agent with the given search tool"""
    agent = LlmAgent(
        name=name,
        description="A helpful assistant for conversation with RAG knowledge",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[search_tool],
    )
    return agent
