# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""TeamAgent setup with LangGraphAgent as a member."""

from typing import Annotated
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import tools_condition
from trpc_agent_sdk.agents import LangGraphAgent
from trpc_agent_sdk.agents import langgraph_llm_node
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.teams import TeamAgent

from .config import get_model_config
from .prompts import CALCULATOR_EXPERT_INSTRUCTION
from .prompts import LEADER_INSTRUCTION
from .tools import calculate


class State(TypedDict):
    """LangGraph state definition."""
    messages: Annotated[list, add_messages]


def _create_model() -> LLMModel:
    """Create a model."""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def build_calculator_graph():
    """Build a LangGraph for math calculations."""

    api_key, url, model_name = get_model_config()

    # Initialize model with deepseek prefix for langchain compatibility
    model = init_chat_model(
        f"deepseek:{model_name}",
        api_key=api_key,
        api_base=url,
    )
    tools = [calculate]
    llm_with_tools = model.bind_tools(tools)

    @langgraph_llm_node
    def calculator_bot(state: State):
        """Calculator chatbot node with tools."""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    # Build graph
    graph_builder = StateGraph(State)
    graph_builder.add_node("calculator", calculator_bot)

    # Add tool node
    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)

    # Add edges
    graph_builder.add_edge(START, "calculator")
    graph_builder.add_conditional_edges("calculator", tools_condition)
    graph_builder.add_edge("tools", "calculator")

    return graph_builder.compile()


def create_team():
    """Create a team with LangGraphAgent as a member.

    This system demonstrates TeamAgent with LangGraph integration:
    - Leader coordinates tasks using LlmAgent
    - LangGraph member executes calculations using LangGraphAgent
    """

    model = _create_model()

    # LangGraph member agent - calculator expert
    calculator_graph = build_calculator_graph()
    langgraph_calculator = LangGraphAgent(
        name="calculator_expert",
        description="Math calculation expert powered by LangGraph, can perform add/subtract/multiply/divide",
        graph=calculator_graph,
        instruction=CALCULATOR_EXPERT_INSTRUCTION,
    )

    # Team leader using LlmAgent
    team = TeamAgent(
        name="math_assistant_team",
        model=model,
        members=[langgraph_calculator],
        instruction=LEADER_INSTRUCTION,
    )

    return team


root_agent = create_team()
