# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from typing import Callable
from typing import Optional
from typing import Union

from trpc_agent_sdk.agents import ChainAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(name: str,
                 description: str,
                 instruction: str,
                 tools: Optional[list[Union[BaseTool, Callable]]] = None) -> LlmAgent:
    """ Create an agent
    Args:
        name: str, the name of the agent
        description: str, the description of the agent
        instruction: str, the instruction of the agent
        tools: Optional[list[Union[BaseTool, Callable]]], the tools of the agent
    Returns:
        LlmAgent, the agent
    """
    new_tools = []
    for tool in tools or []:
        if isinstance(tool, Callable):
            new_tools.append(FunctionTool(tool))
        elif isinstance(tool, BaseTool):
            new_tools.append(tool)
        else:
            raise TypeError(f"Unsupported tool type: {type(tool)}")
    agent = LlmAgent(
        name=name,
        description=description,
        model=_create_model(),  # You can change this to your preferred model
        instruction=instruction,
        tools=new_tools,
    )
    return agent


def create_chain_agent():
    """Create a requirement analysis Agent"""
    analyzer = LlmAgent(
        name="requirement_analyzer",
        description="Analyze user requirements",
        model=_create_model(),
        instruction="You are a requirement analysis expert. Please analyze the user's requirements and summarize the要点 in简洁的语言。",
        output_key="analysis_result",  # Output saved to state
    )
    planner = LlmAgent(
        name="solution_planner",
        description="Develop a solution",
        model=_create_model(),
        instruction="You are a solution planner. Based on the analysis results, develop a solution:\n\n{analysis_result}\n\nPlease provide specific action suggestions.",
        output_key="solution_plan",
    )
    return ChainAgent(
        name="analysis_chain",
        description="Requirement analysis and solution planning chain",
        sub_agents=[analyzer, planner],
    )


root_agent = create_agent(name="assistant", description="A helpful assistant for conversation", instruction=INSTRUCTION)
