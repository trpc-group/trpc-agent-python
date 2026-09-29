# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent used by the TencentDB Agent Memory example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import load_memory_tool

from .config import get_model_config


def create_agent() -> LlmAgent:
    """Create an assistant that can recall cross-session memory."""
    api_key, base_url, model_name = get_model_config()
    return LlmAgent(
        name="memory_assistant",
        description="An assistant with TencentDB Agent Memory.",
        model=OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
        ),
        instruction=("Use load_memory before answering questions about information the "
                     "user may have shared in earlier conversations."),
        tools=[load_memory_tool],
    )


root_agent = create_agent()
