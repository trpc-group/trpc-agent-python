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


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(max_history_messages: int = 0) -> LlmAgent:
    """Create an agent with history limit.

    Args:
        max_history_messages: Maximum number of history messages to include.
                              0 means no limit (default behavior).
    """
    agent = LlmAgent(
        name="assistant",
        description="Agent demonstrating history control",
        model=_create_model(),
        instruction=INSTRUCTION,
        max_history_messages=max_history_messages,
    )
    return agent


root_agent = create_agent()
