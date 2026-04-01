# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
""" Agent module"""

from typing import Annotated, Literal, TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition, ToolNode
from langgraph.types import interrupt, Command, Interrupt
from langgraph.checkpoint.memory import InMemorySaver

from trpc_agent.agents import LangGraphAgent
from trpc_agent.agents import langgraph_llm_node

from .prompts import INSTRUCTION
from .tools import execute_database_operation
from .config import get_model_config


# Compatibility patch: newer LangGraph removed Interrupt.ns, but the
# trpc-agent framework still reads interrupt.ns to extract node name and id.
# Derive ns from the "_node_name" field in interrupt.value + interrupt.id.
if not hasattr(Interrupt, 'ns'):
    def _compat_ns(self):
        name = 'interrupt'
        if isinstance(self.value, dict):
            name = self.value.get('_node_name', name)
        return (f"{name}:{self.id}",)

    Interrupt.ns = property(_compat_ns)


class State(TypedDict):
    messages: Annotated[list, add_messages]
    task_description: str
    approval_status: str


def _build_graph():
    """Build a LangGraph with human-in-the-loop approval using interrupt."""

    api_key, url, model_name = get_model_config()
    model = init_chat_model(
        model_name,
        api_key=api_key,
        api_base=url,
    )
    tools = [execute_database_operation]
    llm_with_tools = model.bind_tools(tools)

    @langgraph_llm_node
    def chatbot(state: State):
        """Chatbot node that can use tools"""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    def human_approval(state: State) -> Command[Literal["approved_path", "rejected_path"]]:
        """Human approval node that interrupts execution for human input."""
        last_message = state["messages"][-1] if state["messages"] else None
        task_info = {
            "_node_name": "human_approval",
            "question": "Do you approve this database operation?",
        }

        if last_message and hasattr(last_message, "tool_calls") and last_message.tool_calls:
            tool_call = last_message.tool_calls[0]
            task_info.update(
                {
                    "operation": tool_call.get("name", "unknown"),
                    "arguments": tool_call.get("args", {}),
                    "tool_call_id": tool_call.get("id", "unknown"),
                }
            )

        decision = interrupt(task_info)
        approval_status = decision.get("status", "rejected")

        if approval_status in ["approved", "approve", "yes", "true"]:
            return Command(goto="approved_path", update={"approval_status": "approved"})
        else:
            return Command(goto="rejected_path", update={"approval_status": "rejected"})

    def approved_node(state: State) -> State:
        """Handle approved operations"""
        print("✅ Operation approved - executing...")
        return {"messages": [{"role": "assistant", "content": "Operation has been approved and will be executed."}]}

    def rejected_node(state: State) -> State:
        """Handle rejected operations"""
        print("❌ Operation rejected - cancelling...")
        return {"messages": [{"role": "assistant", "content": "Operation has been rejected and cancelled."}]}

    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)
    graph_builder.add_node("human_approval", human_approval)
    graph_builder.add_node("approved_path", approved_node)
    graph_builder.add_node("rejected_path", rejected_node)

    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)

    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "human_approval")
    graph_builder.add_edge("approved_path", END)
    graph_builder.add_edge("rejected_path", END)

    checkpointer = InMemorySaver()
    return graph_builder.compile(checkpointer=checkpointer)


def create_agent() -> LangGraphAgent:
    """Create a LangGraph Agent with human-in-the-loop support"""
    graph = _build_graph()

    return LangGraphAgent(
        name="human_in_loop_langgraph_agent",
        description="A LangGraph agent that requires human approval for database operations",
        graph=graph,
        instruction=INSTRUCTION,
    )


root_agent = create_agent()
