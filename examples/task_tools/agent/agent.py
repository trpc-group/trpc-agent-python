# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent module for the Task tools example."""

import os

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import TaskToolSet
from trpc_agent_sdk.tools.file_tools import BashTool
from trpc_agent_sdk.tools.file_tools import ReadTool
from trpc_agent_sdk.tools.file_tools import WriteTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """Create the LLM model used by the demo agent."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_task_agent(work_dir: str | None = None) -> LlmAgent:
    """Build an agent that plans, tracks, and executes multi-step work.

    Args:
        work_dir: Working directory for ``Bash`` / ``Write`` / ``Read``. Defaults to ``os.getcwd()``.

    The toolset exposes ``task_create`` / ``task_update`` / ``task_get`` /
    ``task_list``. The board is persisted to branch-scoped session state and
    survives across ``Runner.run_async`` invocations.
    """
    cwd = work_dir or os.getcwd()
    return LlmAgent(
        name="task_planner",
        description=("Engineering assistant that plans and tracks multi-step projects step by step."),
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            TaskToolSet(),
            BashTool(cwd=cwd),
            WriteTool(cwd=cwd),
            ReadTool(cwd=cwd),
        ],
    )


task_agent = create_task_agent()
root_agent = task_agent
