# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TeamAgent setup demonstrating Human-in-the-Loop functionality."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import LongRunningFunctionTool

from .config import get_model_config
from .prompts import ASSISTANT_INSTRUCTION
from .prompts import LEADER_INSTRUCTION
from .tools import request_approval
from .tools import search_info


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_team():
    """Create a team with HITL support.

    This system demonstrates TeamAgent with Human-in-the-Loop:
    - Leader can trigger approval requests via LongRunningFunctionTool
    - System pauses when HITL is triggered
    - Resumes after human provides approval
    """

    model = _create_model()

    # Simple assistant member
    assistant = LlmAgent(
        name="assistant",
        model=model,
        description="General assistant that can search for information",
        instruction=ASSISTANT_INSTRUCTION,
        tools=[FunctionTool(search_info)],
    )

    # Team with HITL approval tool
    approval_tool = LongRunningFunctionTool(request_approval)

    team = TeamAgent(
        name="hitl_team",
        model=model,
        members=[assistant],
        instruction=LEADER_INSTRUCTION,
        tools=[approval_tool],
    )

    return team


root_agent = create_team()
