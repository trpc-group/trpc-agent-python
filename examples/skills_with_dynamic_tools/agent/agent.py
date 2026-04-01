# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent for the skill run. """

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import INSTRUCTION
from .tools import create_skill_dynamic_tool_set
from .tools import create_skill_tool_set


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent():
    """Create a skill run agent to demonstrate the various capabilities of an LLM agent."""

    # Create tools
    workspace_runtime_type = "local"  # for local runtime
    # workspace_runtime_type = "container" # for container runtime
    skill_tool_set, skill_repository = create_skill_tool_set(workspace_runtime_type=workspace_runtime_type)
    dynamic_tool_set = create_skill_dynamic_tool_set(skill_repository=skill_repository)

    return LlmAgent(
        name="skill_run_agent",
        description="A professional skill run assistant that can use Agent Skills.",
        model=_create_model(),
        # Use state variables for template replacement - Demonstration of the {var} syntax
        instruction=INSTRUCTION,
        tools=[skill_tool_set, dynamic_tool_set],
        skill_repository=skill_repository,
    )


root_agent = create_agent()
