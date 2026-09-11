# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent used by the advanced session compaction example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.sessions.compact import AdvancedAutoCompactSummarizerFilter

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create a model with explicit before-model compaction filtering."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=url,
        filters=[AdvancedAutoCompactSummarizerFilter()],
    )


def create_agent() -> LlmAgent:
    """Create the Python tutor agent."""
    return LlmAgent(
        name="python_tutor",
        description="Python programming tutor that helps users learn Python",
        model=_create_model(),
        instruction=INSTRUCTION,
    )


root_agent = create_agent()
