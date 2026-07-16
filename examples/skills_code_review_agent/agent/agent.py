# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LlmAgent wiring for the code review example."""
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.skills import SkillToolSet

from .config import get_model_config
from .fake_model import FakeReviewModel
from .prompts import INSTRUCTION


def create_review_agent(repository, dry_run: bool, tool_filters: list) -> LlmAgent:
    """Create the review agent. tool_filters guard skill_run via governance."""
    if dry_run:
        model = FakeReviewModel()
    else:
        api_key, url, model_name = get_model_config()
        model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    toolset = SkillToolSet(repository=repository, filters=list(tool_filters))
    return LlmAgent(
        name="code_review_agent",
        description="Automated code review agent combining skill scripts and LLM analysis.",
        model=model,
        instruction=INSTRUCTION,
        tools=[toolset],
        skill_repository=repository,
    )
