# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LlmAgent configuration for the OpenAPI tools example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import create_toolset


def _create_model() -> LLMModel:
    """Create the configured OpenAI-compatible model."""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
    )


def create_agent() -> LlmAgent:
    """Create an Agent backed by tools generated from OpenAPI."""
    return LlmAgent(
        description="Queries and updates a pet service through OpenAPI tools.",
        instruction=INSTRUCTION,
        model=_create_model(),
        name="openapi_pet_agent",
        tools=[create_toolset()],
    )


root_agent = create_agent()
