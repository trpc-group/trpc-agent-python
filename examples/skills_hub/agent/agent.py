# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent that demonstrates fetching a skill from the Skill Hub before running. """

from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .hub import create_skill_tool_set
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(skills_dir: Path) -> LlmAgent:
    """Fetch a skill from GitHub via the Skill Hub, then build an agent that can use it.

    Args:
        skills_dir: Local directory to install fetched skills into. Populated
            by `create_default_skill_repository(additional_skill_specs=...)`
            before the agent is constructed.
    """
    skill_tool_set, skill_repository = create_skill_tool_set(skills_dir)

    return LlmAgent(
        name="skill_hub_demo_agent",
        description="An assistant that fetches skills on demand from the Skill Hub.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[skill_tool_set],
        skill_repository=skill_repository,
    )
