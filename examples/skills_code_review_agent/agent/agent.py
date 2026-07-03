# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Construct the code-review LlmAgent (Skills + tool), used by run_agent.py."""
from __future__ import annotations

from trpc_agent_sdk.agents import LlmAgent

from .config import get_model
from .prompts import INSTRUCTION
from .tools import build_review_tool


def create_agent() -> LlmAgent:
    """An LlmAgent that reviews a diff by calling the review_code tool, then summarizes.

    The guard Filter (slice 3) attaches on the tool via ``filters_name`` — a TOOL-scoped filter,
    not on the agent (which resolves in the AGENT namespace and would raise).
    """
    return LlmAgent(
        name="code_review_agent",
        model=get_model(),
        instruction=INSTRUCTION,
        tools=[build_review_tool()],
    )
