# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LlmAgent configuration for the artifact service example."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import list_reports
from .tools import load_report
from .tools import save_report


def _create_model() -> LLMModel:
    """Create the configured OpenAI-compatible model."""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
    )


def create_agent() -> LlmAgent:
    """Create the report agent and expose artifact tools."""
    return LlmAgent(
        description="Creates and retrieves persistent Markdown reports.",
        instruction=INSTRUCTION,
        model=_create_model(),
        name="artifact_report_agent",
        tools=[
            FunctionTool(save_report),
            FunctionTool(list_reports),
            FunctionTool(load_report),
        ],
    )


root_agent = create_agent()
