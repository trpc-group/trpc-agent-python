# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent module for the TodoWriteTool + Human-in-the-Loop example."""

import os
from typing import List
from typing import Optional

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import LongRunningFunctionTool
from trpc_agent_sdk.tools import TodoItem
from trpc_agent_sdk.tools import TodoStatus
from trpc_agent_sdk.tools import TodoWriteTool
from trpc_agent_sdk.tools.file_tools import BashTool
from trpc_agent_sdk.tools.file_tools import ReadTool
from trpc_agent_sdk.tools.file_tools import WriteTool

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import request_todo_plan_approval


def _create_model() -> LLMModel:
    """Create the LLM model used by the demo agent."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def _all_done_nudge_hook(old: List[TodoItem], new: List[TodoItem]) -> Optional[str]:
    """Example read-only NudgeHook (aligned with Go ``examples/todo``).

    When the plan has at least three items and every item is ``completed``,
    append a reminder so the model summarises the outcome before wrapping up.
    """
    if len(new) < 3:
        return None
    if not all(item.status == TodoStatus.COMPLETED for item in new):
        return None
    return ("Reminder: all tasks are marked completed. "
            "Before finishing, briefly summarise the outcome for the user.")


def create_todo_agent(work_dir: str | None = None) -> LlmAgent:
    """Build an agent with HITL todo planning plus file/shell execution tools.

    Args:
        work_dir: Working directory for ``Bash`` / ``Write`` / ``Read``. Defaults to ``os.getcwd()``.

    ``clear_on_all_done=False`` keeps completed items visible so the demo
    can render the final all-done checklist; production agents may keep
    the default (``True``) to avoid stale items piling up across turns.
    """
    cwd = work_dir or os.getcwd()
    todo_tool = TodoWriteTool(
        clear_on_all_done=False,
        nudge_hooks=[_all_done_nudge_hook],
    )
    plan_approval_tool = LongRunningFunctionTool(request_todo_plan_approval)
    bash_tool = BashTool(cwd=cwd)
    write_tool = WriteTool(cwd=cwd)
    read_tool = ReadTool(cwd=cwd)

    return LlmAgent(
        name="todo_planner",
        description="Engineering assistant that plans and tracks multi-step tasks.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[plan_approval_tool, todo_tool, bash_tool, write_tool, read_tool],
    )


todo_agent = create_todo_agent()
root_agent = todo_agent
