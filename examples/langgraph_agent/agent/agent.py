# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" LangGraph calculator agent with subgraph support. """

from typing import Annotated
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import tools_condition
from trpc_agent_sdk.agents import LangGraphAgent
from trpc_agent_sdk.agents import langgraph_llm_node

from .config import get_model_config
from .tools import calculate


# Define state structure
class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_calculator_subgraph():
    """Build a calculator subgraph that handles math operations.

    This subgraph demonstrates:
    1. Tool calling flow for calculations
    2. Nested graph structure for modular design
    """
    # Initialize model
    api_key, url, model_name = get_model_config()
    model = init_chat_model(
        model_name,
        api_key=api_key,
        api_base=url,
    )

    tools = [calculate]
    llm_with_tools = model.bind_tools(tools)

    @langgraph_llm_node
    def calculator_llm(state: State):
        """Calculator LLM node that uses the calculate tool."""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    # Build subgraph
    subgraph_builder = StateGraph(State)
    subgraph_builder.add_node("calculator_llm", calculator_llm)

    # Add tool node
    tool_node = ToolNode(tools=tools)
    subgraph_builder.add_node("tools", tool_node)

    # Add edges
    subgraph_builder.add_edge(START, "calculator_llm")
    subgraph_builder.add_conditional_edges("calculator_llm", tools_condition)
    subgraph_builder.add_edge("tools", "calculator_llm")

    return subgraph_builder.compile()


def build_graph_with_subgraph():
    """Build a LangGraph with subgraph for testing subgraph streaming.

    This graph demonstrates:
    1. Parent graph with routing logic
    2. Calculator subgraph for math operations
    3. Subgraph streaming support
    """
    # Initialize model for router
    api_key, url, model_name = get_model_config()
    model = init_chat_model(
        model_name,
        api_key=api_key,
        api_base=url,
    )

    # Build calculator subgraph
    calculator_subgraph = build_calculator_subgraph()

    @langgraph_llm_node
    def router(state: State):
        """Router node that decides whether to use calculator or respond directly."""
        # Simple routing: check if the message contains math-related keywords
        return {"messages": [model.invoke(state["messages"])]}

    def should_use_calculator(state: State) -> str:
        """Determine if we should route to calculator subgraph."""
        last_message = state["messages"][-1].content if state["messages"] else ""
        math_keywords = ["calculate", "+", "-", "*", "/", "="]
        if any(keyword in last_message.lower() for keyword in math_keywords):
            return "calculator"
        return END

    # Build parent graph
    graph_builder = StateGraph(State)
    graph_builder.add_node("router", router)
    graph_builder.add_node("calculator", calculator_subgraph)

    # Add edges
    graph_builder.add_edge(START, "router")
    graph_builder.add_conditional_edges("router", should_use_calculator)
    graph_builder.add_edge("calculator", END)

    return graph_builder.compile()


def build_graph():
    """Build a simple LangGraph for basic agent functionality.

    This graph demonstrates:
    1. LLM node with tool binding
    2. Tool execution via ToolNode
    3. Conditional edges for tool calling flow
    """

    # Initialize model
    api_key, url, model_name = get_model_config()
    model = init_chat_model(
        model_name,
        api_key=api_key,
        api_base=url,
    )

    tools = [calculate]
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
    """Create a simple LangGraph agent.

    This agent demonstrates:
    - Basic LangGraph integration with trpc_agent
    - Tool calling with @langgraph_tool_node decorator
    - LLM node with @langgraph_llm_node decorator
    """
    graph = build_graph()

    return LangGraphAgent(
        name="simple_langgraph_agent",
        description="A calculator assistant that can perform basic math operations.",
        graph=graph,
        instruction="""You are a helpful assistant that can:
1. Have friendly conversations
2. Perform calculations using the calculate tool (add, subtract, multiply, divide)

Be professional and helpful in your responses.""",
    )


def create_agent_with_subgraph():
    """Create a LangGraph agent with subgraph support.

    This agent demonstrates:
    - Subgraph streaming with trpc_agent
    - Parent graph with routing logic
    - Calculator subgraph for math operations

    To enable subgraph streaming, use run_config:
        run_config=RunConfig(agent_run_config={"subgraphs": True})
    """
    graph = build_graph_with_subgraph()

    return LangGraphAgent(
        name="langgraph_agent_with_subgraph",
        description="A calculator assistant with subgraph support for math operations.",
        graph=graph,
        instruction="""You are a helpful assistant that can:
1. Have friendly conversations
2. Perform calculations when asked (add, subtract, multiply, divide)

When the user asks for calculations, route to the calculator for accurate results.
Be professional and helpful in your responses.""",
    )


root_agent = create_agent()
# root_agent = create_agent_with_subgraph()
