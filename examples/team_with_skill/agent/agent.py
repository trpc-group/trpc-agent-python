# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TeamAgent setup with leader skill capability."""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import LEADER_INSTRUCTION
from .prompts import RESEARCHER_INSTRUCTION
from .prompts import WRITER_INSTRUCTION
from .tools import check_grammar
from .tools import create_skill_tool_set
from .tools import get_current_date
from .tools import search_web


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_team() -> TeamAgent:
    """Create a team where leader can use skills before delegation."""
    model = _create_model()
    skill_tool_set, skill_repository = create_skill_tool_set(workspace_runtime_type="local")

    researcher = LlmAgent(
        name="researcher",
        model=model,
        description="Research expert",
        instruction=RESEARCHER_INSTRUCTION,
        tools=[FunctionTool(search_web)],
    )

    writer = LlmAgent(
        name="writer",
        model=model,
        description="Writing expert",
        instruction=WRITER_INSTRUCTION,
        tools=[FunctionTool(check_grammar)],
    )

    return TeamAgent(
        name="content_team_with_skill",
        model=model,
        members=[researcher, writer],
        instruction=LEADER_INSTRUCTION,
        share_member_interactions=True,
        tools=[FunctionTool(get_current_date), skill_tool_set],
        skill_repository=skill_repository,
    )


root_agent = create_team()
