# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent for the Advanced Memory Redis session example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import large_report


def _create_model() -> LLMModel:
    """Create the configured model."""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
    )


def create_agent() -> LlmAgent:
    """Create the report Agent used by the session example."""
    return LlmAgent(
        name="redis_compression_demo",
        description="Demonstrate Redis session context compression.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[FunctionTool(large_report)],
    )


root_agent = create_agent()
