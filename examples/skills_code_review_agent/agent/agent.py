# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CodeReviewAgent — LlmAgent for the code review pipeline.

Wraps the existing review_agent.run_review() pipeline as a FunctionTool,
enabling interactive multi-turn code review sessions via A2A or AG-UI.
"""

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import load_memory_tool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import run_code_review_tool

# Try to load the knowledge search tool; gracefully handle missing deps
_knowledge_tool = None
try:
    from ..knowledge import knowledge_search_tool as _kst
    _knowledge_tool = _kst
except ImportError:
    pass


def _create_model() -> LLMModel:
    """Create a model instance from environment variables."""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
    )


def create_agent() -> LlmAgent:
    """Create the CodeReviewAgent.

    The agent wraps the existing review_agent.run_review() pipeline
    as a FunctionTool, allowing the LLM to invoke it when the user
    provides a diff for review.

    Returns:
        A configured LlmAgent instance.
    """
    return LlmAgent(
        name="CodeReviewAgent",
        description=(
            "A professional code review assistant that analyzes code changes, "
            "detects security risks, resource leaks, and other issues, "
            "and generates structured review reports."
        ),
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            run_code_review_tool,
            load_memory_tool,
            _knowledge_tool,
        ] if _knowledge_tool else [
            run_code_review_tool,
            load_memory_tool,
        ],
    )


root_agent = create_agent()