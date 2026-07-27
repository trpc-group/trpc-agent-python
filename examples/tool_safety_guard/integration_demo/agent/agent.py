# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Wire LlmAgent with all four safety-guarded execution surfaces."""

from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import (
    create_bash_tool,
    create_code_executor,
    create_mcp_toolset,
    create_safety_filter,
    create_safety_scanner,
    create_skill_toolset,
)


def create_agent(*, block_on_review: bool = False) -> LlmAgent:
    """Create an agent with safety guard on all four execution paths.

    Args:
        block_on_review: If True, NEEDS_HUMAN_REVIEW also blocks execution.
    """
    api_key, base_url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)

    scanner, policy = create_safety_scanner()
    safety_filter = create_safety_filter(scanner, block_on_review=block_on_review)

    return LlmAgent(
        name="tool_safety_agent",
        description="Runs tool, skill, MCP, and code executor safety scenarios.",
        model=model,
        instruction=INSTRUCTION,
        tools=[
            create_bash_tool(scanner, block_on_review=block_on_review),
            create_skill_toolset(safety_filter, policy=policy, block_on_review=block_on_review),
            create_mcp_toolset(safety_filter),
        ],
        code_executor=create_code_executor(scanner, block_on_review=block_on_review),
    )
