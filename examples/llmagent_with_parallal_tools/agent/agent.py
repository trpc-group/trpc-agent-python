#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import os

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .prompts import INSTRUCTION
from .tools import HobbyToolSet

# =============================================================================
# 2. Create Agent
# =============================================================================


def _get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables"""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')
    url = os.getenv('TRPC_AGENT_BASE_URL', '')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')
    if not api_key or not url or not model_name:
        raise ValueError('''TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL,
                         and TRPC_AGENT_MODEL_NAME must be set in environment variables''')
    return api_key, url, model_name


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = _get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create an agent related to hobbies"""

    model = _create_model()

    # Get the registered toolkit
    hobby_toolset = HobbyToolSet()

    # Initialize the Toolkit
    if hobby_toolset:
        hobby_toolset.initialize()

    return LlmAgent(
        name="hobby_toolset_agent",
        description="""A virtual person who loves life, select the appropriate tool based on the user's 
        interest to obtain interest information, and provide friendly replies.""",
        model=model,
        tools=[hobby_toolset],
        parallel_tool_calls=True,
        instruction=INSTRUCTION,
    )


root_agent = create_agent()
