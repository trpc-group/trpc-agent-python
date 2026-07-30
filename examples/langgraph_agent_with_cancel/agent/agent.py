# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" LangGraph calculator agent with cancellation support. """

from typing import Annotated
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import tools_condition
from trpc_agent_sdk.agents import LangGraphAgent
from trpc_agent_sdk.agents import langgraph_llm_node

from .config import get_model_config
from .tools import analyze_data
from .tools import calculate


# Define state structure
class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_graph():
    """Build a LangGraph with cancellation support.

    This graph demonstrates:
    1. Cancellation during LLM streaming (checkpoint in agent)
    2. Cancellation during tool execution (checkpoint in agent)

    The LangGraphAgent has checkpoints at:
    - Method entry
    - Each chunk iteration during streaming
    """

    # Initialize model
    api_key, url, model_name = get_model_config()
    model = init_chat_model(
        model_name,
        api_key=api_key,
        api_base=url,
    )

    tools = [calculate, analyze_data]
    llm_with_tools = model.bind_tools(tools)

    # Define LLM node with @langgraph_llm_node decorator
    @langgraph_llm_node
    def chatbot(state: State):
        """Chatbot node that can use tools."""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    # Build graph
    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)

    # Add tool node
    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)

    # Add edges
    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "chatbot")

    return graph_builder.compile()


def create_agent():
    """Create LangGraph agent with cancellation support.

    This agent demonstrates cooperative cancellation at various checkpoints:
    - At method entry (before graph execution)
    - At each chunk iteration during streaming
    - Tool execution can be cancelled when checkpoints are hit

    The tools have delays (2-3 seconds) to simulate slow operations,
    giving enough time to cancel during execution.
    """
    graph = build_graph()

    return LangGraphAgent(
        name="calculator_agent_with_cancel",
        description="A calculator and data analysis assistant that supports cancellation at any time.",
        graph=graph,
        instruction="""You are a helpful assistant that can:
1. Perform calculations using the calculate tool
2. Analyze data using the analyze_data tool

Your responses may be cancelled by the user at any time. This is normal behavior
and the cancellation mechanism works at various checkpoints including during
tool execution and LLM streaming.

Be professional and helpful in your responses.""",
    )


root_agent = create_agent()
