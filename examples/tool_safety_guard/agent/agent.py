# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent wired with a BashTool guarded by the Tool Safety Guard filter.

The filter is attached by name (``filters_name=["tool_safety_guard"]``); the
mere import of ``trpc_agent_sdk.tools.safety`` registers it. When the model asks
Bash to run something dangerous, the filter scans the command first and blocks
it before the shell ever runs, returning a structured refusal to the model.
"""

from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import BashTool

# Importing the safety package registers the "tool_safety_guard" tool filter.
import trpc_agent_sdk.tools.safety  # noqa: F401

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create the chat model from environment configuration."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_agent() -> LlmAgent:
    """Create an agent whose BashTool is guarded by the safety filter."""
    bash_tool = BashTool(
        cwd=str(Path(__file__).resolve().parent),
        filters_name=["tool_safety_guard"],
    )
    return LlmAgent(
        name="devops_assistant",
        description="A DevOps assistant with a safety-guarded Bash tool",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[bash_tool],
    )


root_agent = create_agent()
