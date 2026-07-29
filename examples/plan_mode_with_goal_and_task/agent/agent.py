# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.

from __future__ import annotations

import os

from dotenv import load_dotenv
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents.sub_agent import EXPLORE_AGENT
from trpc_agent_sdk.agents.sub_agent import PLAN_AGENT
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.plan_mode import setup_plan
from trpc_agent_sdk.tools import FileToolSet
from trpc_agent_sdk.tools import SpawnSubAgentTool
from trpc_agent_sdk.tools import TaskToolSet
from trpc_agent_sdk.tools.goal_tools import GoalOptions
from trpc_agent_sdk.tools.goal_tools import setup_goal

from .prompts import SYSTEM_INSTRUCTION

load_dotenv()


def create_plan_goal_task_agent() -> LlmAgent:
    model = OpenAIModel(
        model_name=os.environ.get("TRPC_AGENT_MODEL_NAME", "gpt-4.1-mini"),
        api_key=os.environ.get("TRPC_AGENT_API_KEY", ""),
        base_url=os.environ.get("TRPC_AGENT_BASE_URL"),
    )
    agent = LlmAgent(
        name="orchestrator",
        model=model,
        instruction=SYSTEM_INSTRUCTION,
        tools=[
            FileToolSet(),
            SpawnSubAgentTool(agents=[EXPLORE_AGENT, PLAN_AGENT]),
            TaskToolSet(),
        ],
    )
    setup_plan(agent)
    setup_goal(agent, GoalOptions(max_retries=3))
    return agent
