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


def create_agent(*, recall_enabled: bool = True) -> LlmAgent:
    """Create an assistant, optionally with cross-session recall."""
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
                     "user may have shared in earlier conversations."
                     if recall_enabled else "Acknowledge the user's new information without recalling prior memory."),
        tools=[load_memory_tool] if recall_enabled else [],
    )


root_agent = create_agent()
