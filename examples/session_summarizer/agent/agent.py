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


def create_agent() -> LlmAgent:
    """ Create an agent"""
    agent = LlmAgent(
        name="python_tutor",
        description="Python编程导师，帮助用户学习Python编程",
        model=_create_model(),  # You can change this to your preferred model
        instruction=INSTRUCTION,
        # 如果期望在 agent 中使用 session summarizer，可以在这里绑定 filter
        # filters=[AgentSessionSummarizerFilter(_create_model())],
    )
    return agent


root_agent = create_agent()
