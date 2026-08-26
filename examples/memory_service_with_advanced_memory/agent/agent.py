# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent definition for the Advanced Memory example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION


def create_agent() -> LlmAgent:
    """Create an agent; the Runner installs Advanced Memory tools."""
    api_key, base_url, model_name = get_model_config()
    return LlmAgent(
        name="advanced_memory_assistant",
        description="A minimal Advanced Memory demonstration assistant",
        model=OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
        ),
        instruction=INSTRUCTION,
    )
