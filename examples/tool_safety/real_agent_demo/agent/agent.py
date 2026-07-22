# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Real LlmAgent wiring for the tool-safety review demo."""

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import create_bash_tool
from .tools import create_code_executor
from .tools import create_mcp_toolset
from .tools import create_safety_filter
from .tools import create_safety_scanner
from .tools import create_skill_toolset


def _create_model() -> LLMModel:
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def create_agent(*, block_on_review: bool = False, model: LLMModel | None = None) -> LlmAgent:
    """Create an agent that reaches all safety-guarded execution boundaries."""
    scanner = create_safety_scanner()
    safety_filter = create_safety_filter(scanner, block_on_review=block_on_review)

    return LlmAgent(
        name="tool_safety_real_agent",
        description="Runs real tool, skill, MCP tool, and code executor safety scenarios.",
        model=model or _create_model(),
        instruction=INSTRUCTION,
        tools=[
            create_bash_tool(scanner, block_on_review=block_on_review),
            create_skill_toolset(safety_filter),
            create_mcp_toolset(safety_filter),
        ],
        code_executor=create_code_executor(scanner, block_on_review=block_on_review),
    )
