# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent module for the Goal tools example."""

import os

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools.file_tools import BashTool
from trpc_agent_sdk.tools.file_tools import ReadTool
from trpc_agent_sdk.tools.file_tools import WriteTool
from trpc_agent_sdk.tools.goal_tools import GoalOptions
from trpc_agent_sdk.tools.goal_tools import RetryEvent
from trpc_agent_sdk.tools.goal_tools import setup_goal

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def on_retry(event: RetryEvent) -> None:
    """Observability callback: called every time the retry intercepts a premature final."""
    if event.reason == "blocked":
        print(
            f"  ⚡ [Goal retry] Premature final intercepted "
            f"(attempt {event.attempt_number}/{event.max_retries}). "
            f"Objective: {event.goal.objective!r}"
        )
    else:
        print(
            f"  ⚠️  [Goal retry] Budget exhausted ({event.max_retries} retries). "
            f"Letting final response through."
        )


def create_goal_agent(work_dir: str | None = None) -> LlmAgent:
    """Build an agent with the Goal capability mounted via ``setup_goal``.

    The agent exposes ``create_goal`` / ``get_goal`` / ``update_goal`` tools
    plus file-system tools for actually executing multi-step work.  The
    retry callbacks (guidance injection + premature-final interception)
    are installed automatically by :func:`setup_goal`.

    Args:
        work_dir: Working directory for Bash / Write / Read tools.
    """
    cwd = work_dir or os.getcwd()
    agent = LlmAgent(
        name="goal_agent",
        description="Engineering assistant that pursues a persistent session goal step by step.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            BashTool(cwd=cwd),
            WriteTool(cwd=cwd),
            ReadTool(cwd=cwd),
        ],
    )
    opts = GoalOptions(
        max_retries=3,
        on_retry=on_retry,
    )
    setup_goal(agent, opts)
    return agent


goal_agent = create_goal_agent()
root_agent = goal_agent
