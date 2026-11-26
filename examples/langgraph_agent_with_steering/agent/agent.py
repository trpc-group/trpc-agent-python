# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" LangGraph weather agent with steering support. """

from typing import Annotated
from typing import Optional
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import tools_condition
from langgraph.types import Command
from trpc_agent_sdk.agents import LangGraphAgent
from trpc_agent_sdk.agents import get_agent_context
from trpc_agent_sdk.agents import langgraph_llm_node
from trpc_agent_sdk.cancel import mark_steering_content

from .config import get_model_config
from .tools import get_weather_report


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


async def _take_steering(config: RunnableConfig) -> Optional[HumanMessage]:
    """Drain pending steering and build the message to append to graph state."""
    ctx = get_agent_context(config)

    content = await ctx.consume_steering()
    if content is None:
        return None

    marked = mark_steering_content(content)
    text = "".join(part.text or "" for part in marked.parts)
    return HumanMessage(content=text)


async def steering_node(state: State, config: RunnableConfig) -> dict:
    """Consume pending steering on the tool-call branch before the next LLM call."""
    message = await _take_steering(config)
    if message is None:
        return {}
    return {"messages": [message]}


async def tail_steering_node(state: State, config: RunnableConfig) -> Command:
    """Take one last look at steering before the graph ends.

    A message that arrives while the final LLM call is running has no tool node
    left to consume it. Deliver it here and go back to the LLM; otherwise END.
    """
    message = await _take_steering(config)
    if message is None:
        return Command(goto=END)
    return Command(update={"messages": [message]}, goto="chatbot")


def route_after_chatbot(state: State) -> str:
    """Send tool calls to the tools node; drain steering before ending otherwise."""
    return "tools" if tools_condition(state) == "tools" else "tail_steering"


def build_graph():
    """Build a ReAct graph that consumes steering on both exits of the LLM node."""
    api_key, url, model_name = get_model_config()
    model = init_chat_model(
        model_name,
        model_provider="openai",
        api_key=api_key,
        base_url=url,
    )

    tools = [get_weather_report]
    llm_with_tools = model.bind_tools(tools)

    @langgraph_llm_node
    def chatbot(state: State):
        """Chatbot node that can call the weather tool."""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)
    graph_builder.add_node("tools", ToolNode(tools=tools))
    graph_builder.add_node("steering", steering_node)
    graph_builder.add_node("tail_steering", tail_steering_node, destinations=("chatbot", END))

    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges(
        "chatbot",
        route_after_chatbot,
        {
            "tools": "tools",
            "tail_steering": "tail_steering"
        },
    )
    graph_builder.add_edge("tools", "steering")
    graph_builder.add_edge("steering", "chatbot")

    return graph_builder.compile()


def create_agent():
    """Create a LangGraph agent that consumes steering in custom nodes."""
    graph = build_graph()

    return LangGraphAgent(
        name="weather_agent_with_steering",
        description="A weather assistant that accepts steering messages while running.",
        graph=graph,
        instruction="""You are a weather assistant that can query city weather.

If the user sends a steering message that corrects the city while a run is in
progress, follow the latest correction and call the weather tool again for the
corrected city. Be concise and helpful.""",
    )


root_agent = create_agent()
