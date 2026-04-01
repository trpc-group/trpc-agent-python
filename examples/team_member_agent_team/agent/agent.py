# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Hierarchical TeamAgent setup demonstrating TeamAgent as member.

This example shows a nested team structure:
- project_manager (TeamAgent - top level)
  - dev_team (TeamAgent - nested as member)
    - backend_dev (LlmAgent)
    - frontend_dev (LlmAgent)
  - doc_writer (LlmAgent)
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import BACKEND_DEV_INSTRUCTION
from .prompts import DEV_TEAM_LEADER_INSTRUCTION
from .prompts import DOC_WRITER_INSTRUCTION
from .prompts import FRONTEND_DEV_INSTRUCTION
from .prompts import PROJECT_MANAGER_INSTRUCTION
from .tools import design_api
from .tools import design_ui
from .tools import format_docs


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_hierarchical_team():
    """Create a hierarchical team with nested TeamAgent.

    This demonstrates TeamAgent as a member of another TeamAgent:
    - project_manager coordinates dev_team and doc_writer
    - dev_team (itself a TeamAgent) coordinates backend_dev and frontend_dev
    """

    model = _create_model()

    # === Level 2: Development Team Members (LlmAgents) ===

    # Backend developer - handles API and server-side tasks
    backend_dev = LlmAgent(
        name="backend_dev",
        model=model,
        description="Backend development expert for API and server-side implementation",
        instruction=BACKEND_DEV_INSTRUCTION,
        tools=[FunctionTool(design_api)],
    )

    # Frontend developer - handles UI and client-side tasks
    frontend_dev = LlmAgent(
        name="frontend_dev",
        model=model,
        description="Frontend development expert for UI and client-side implementation",
        instruction=FRONTEND_DEV_INSTRUCTION,
        tools=[FunctionTool(design_ui)],
    )

    # === Level 1: Development Team (Nested TeamAgent) ===

    # This TeamAgent will be used as a member of the top-level team
    dev_team = TeamAgent(
        name="dev_team",
        model=model,
        description="Development team handling all technical implementation tasks",
        members=[backend_dev, frontend_dev],
        instruction=DEV_TEAM_LEADER_INSTRUCTION,
        share_member_interactions=True,
    )

    # === Level 1: Documentation Writer (LlmAgent) ===

    doc_writer = LlmAgent(
        name="doc_writer",
        model=model,
        description="Technical documentation writer",
        instruction=DOC_WRITER_INSTRUCTION,
        tools=[FunctionTool(format_docs)],
    )

    # === Level 0: Project Manager (Top-level TeamAgent) ===

    # Top-level team that includes another TeamAgent (dev_team) as a member
    project_manager = TeamAgent(
        name="project_manager",
        model=model,
        members=[dev_team, doc_writer],  # dev_team is a TeamAgent!
        instruction=PROJECT_MANAGER_INSTRUCTION,
        share_member_interactions=True,
    )

    return project_manager


root_agent = create_hierarchical_team()
