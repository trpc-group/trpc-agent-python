# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Orchestrator configurations demonstrating DynamicSubAgentTool.

Two configurations are provided, selectable via ``--mode`` on the command line:

- ``minimal`` — workspace tools and ``dynamic_subagent`` are both registered on
  the orchestrator; the sub-agent inherits the parent surface and the model
  narrows tools per call.
- ``bounded`` — only ``dynamic_subagent`` is registered; workspace tools live
  behind ``DynamicSubAgentTool(tools=...)`` as the capability surface.
"""

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import DynamicSubAgentTool
from trpc_agent_sdk.agents.sub_agent import SubAgentConfig
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .prompts import BOUNDED_ORCHESTRATOR_INSTRUCTION
from .prompts import MINIMAL_ORCHESTRATOR_INSTRUCTION
from .tools import create_workspace_tools


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_minimal_agent() -> LlmAgent:
    """Orchestrator with workspace tools + dynamic_subagent (Go ``-mode=minimal``)."""
    workspace_tools = create_workspace_tools()
    return LlmAgent(
        name="orchestrator",
        description="Orchestrator that delegates focused subtasks to short-lived sub-agents.",
        model=_create_model(),
        instruction=MINIMAL_ORCHESTRATOR_INSTRUCTION,
        generate_content_config=GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=2000,
        ),
        tools=workspace_tools + [
            DynamicSubAgentTool(
                # Stream the sub-agent's execution to the parent consumer.
                agent_config=SubAgentConfig(forward_events=True),
            ),
        ],
    )


def create_bounded_agent() -> LlmAgent:
    """Orchestrator with only dynamic_subagent (Go ``-mode=bounded``)."""
    workspace_tools = create_workspace_tools()
    return LlmAgent(
        name="orchestrator",
        description="Orchestrator that delegates every subtask via dynamic_subagent.",
        model=_create_model(),
        instruction=BOUNDED_ORCHESTRATOR_INSTRUCTION,
        generate_content_config=GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=2000,
        ),
        tools=[
            DynamicSubAgentTool(
                tools=tuple(workspace_tools),
                agent_config=SubAgentConfig(
                    generate_content_config=GenerateContentConfig(
                        temperature=0.3,
                        max_output_tokens=1000,
                    ),
                    # Stream the sub-agent's execution to the parent consumer.
                    forward_events=True,
                ),
            ),
        ],
    )


root_agent = create_minimal_agent()
