# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Agent module"""

from trpc_agent.agents import LlmAgent
from trpc_agent.models import LLMModel
from trpc_agent.models import OpenAIModel
from trpc_agent.tools import LongRunningFunctionTool

from .prompts import MAIN_AGENT_INSTRUCTION, SUB_AGENT_INSTRUCTION
from .tools import human_approval_required, check_system_critical_operation
from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> LlmAgent:
    """ Create an agent with Long Running Function Tools and Sub-Agents"""
    model = _create_model()

    approval_tool = LongRunningFunctionTool(human_approval_required)
    critical_operation_tool = LongRunningFunctionTool(check_system_critical_operation)

    system_operations_agent = LlmAgent(
        name="system_operations_agent",
        model=model,
        description="System operations specialist that handles critical operations requiring human approval",
        instruction=SUB_AGENT_INSTRUCTION,
        tools=[critical_operation_tool],
        disallow_transfer_to_parent=True,
        output_key="system_ops_result",
    )

    agent = LlmAgent(
        name="human_in_loop_agent",
        description="Agent demonstrating long-running tools with human-in-the-loop and sub-agents",
        model=model,
        instruction=MAIN_AGENT_INSTRUCTION,
        tools=[approval_tool],
        sub_agents=[system_operations_agent],
    )
    return agent


root_agent = create_agent()
