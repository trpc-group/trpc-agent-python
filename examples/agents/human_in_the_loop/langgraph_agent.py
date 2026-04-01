#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Annotated
from typing import Literal
from typing import Optional
from typing import TypedDict
from typing_extensions import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt import tools_condition
from langgraph.types import Command
from langgraph.types import interrupt
from trpc_agent_sdk.agents import LangGraphAgent
from trpc_agent_sdk.agents import langgraph_llm_node
from trpc_agent_sdk.agents import langgraph_tool_node
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


# Define state structure
class State(TypedDict):
    messages: Annotated[list, add_messages]
    task_description: str
    approval_status: str


# Define tools
@tool
@langgraph_tool_node
def execute_database_operation(operation: str, database: str, details: dict) -> str:
    """Execute a database operation that requires approval.

    Args:
        operation: The type of operation ('delete', 'update', 'create')
        database: The database name
        details: Additional operation details
    """
    return f"Database operation '{operation}' on '{database}' executed successfully with details: {details}"


@dataclass
class InvocationParams:
    """Parameters for running an invocation"""

    user_id: str
    session_id: str
    agent: LangGraphAgent
    session_service: InMemorySessionService
    app_name: str


def build_graph():
    """Build a LangGraph with human-in-the-loop approval using interrupt."""

    # Initialize model
    model = init_chat_model(
        "deepseek:deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        api_base="http://v2.open.venus.woa.com/llmproxy",
    )
    tools = [execute_database_operation]
    llm_with_tools = model.bind_tools(tools)

    # Define LLM node
    @langgraph_llm_node
    def chatbot(state: State):
        """Chatbot node that can use tools"""
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    # Human approval node using LangGraph interrupt
    def human_approval(state: State) -> Command[Literal["approved_path", "rejected_path"]]:
        """Human approval node that interrupts execution for human input."""

        # Extract task information from the last message
        last_message = state["messages"][-1] if state["messages"] else None
        task_info = {
            "question": "Do you approve this database operation?",
        }

        if last_message and hasattr(last_message, "tool_calls") and last_message.tool_calls:
            # Extract details from tool call
            tool_call = last_message.tool_calls[0]
            task_info.update({
                "operation": tool_call.get("name", "unknown"),
                "arguments": tool_call.get("args", {}),
                "tool_call_id": tool_call.get("id", "unknown"),
            })

        # Interrupt execution and wait for human input
        decision = interrupt(task_info)

        approval_status = decision.get("status", "rejected")

        if approval_status in ["approved", "approve", "yes", "true"]:
            return Command(goto="approved_path", update={"approval_status": "approved"})
        else:
            return Command(goto="rejected_path", update={"approval_status": "rejected"})

    # Approved path - execute the operation
    def approved_node(state: State) -> State:
        """Handle approved operations"""
        print("✅ Operation approved - executing...")
        return {"messages": [{"role": "assistant", "content": "Operation has been approved and will be executed."}]}

    # Rejected path - cancel the operation
    def rejected_node(state: State) -> State:
        """Handle rejected operations"""
        print("❌ Operation rejected - cancelling...")
        return {"messages": [{"role": "assistant", "content": "Operation has been rejected and cancelled."}]}

    # Build the graph
    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)
    graph_builder.add_node("human_approval", human_approval)
    graph_builder.add_node("approved_path", approved_node)
    graph_builder.add_node("rejected_path", rejected_node)

    # Add tool node
    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)

    # Add edges
    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "human_approval")
    graph_builder.add_edge("approved_path", END)
    graph_builder.add_edge("rejected_path", END)

    # Use checkpointer for interrupt support
    checkpointer = InMemorySaver()
    return graph_builder.compile(checkpointer=checkpointer)


def create_agent():
    """Create a LangGraph Agent with human-in-the-loop support"""
    graph = build_graph()

    return LangGraphAgent(
        name="human_in_loop_langgraph_agent",
        description="A LangGraph agent that requires human approval for database operations",
        graph=graph,
        instruction="""
You are a database management assistant that requires human approval for all operations.

When a user requests a database operation:
1. Use the execute_database_operation tool to prepare the operation
2. The system will automatically request human approval
3. Only proceed if the human approves the operation

Always be clear about what operation you're about to perform and why it needs approval.
""",
    )


async def run_invocation(
    params: InvocationParams,
    content: Content,
) -> Optional[LongRunningEvent]:
    """Run an invocation with a fresh runner instance.

    Args:
        params: Invocation parameters containing user_id, session_id, agent, and session_service
        content: The content to send to the agent

    Returns:
        LongRunningEvent if one is encountered, None otherwise
    """
    # Create a new runner for each invocation
    runner = Runner(app_name=params.app_name, agent=params.agent, session_service=params.session_service)

    captured_long_running_event = None

    try:
        async for event in runner.run_async(user_id=params.user_id, session_id=params.session_id, new_message=content):
            if isinstance(event, LongRunningEvent):
                # Capture the long-running event
                captured_long_running_event = event
                print(f"\n🔄 [Long-running operation detected]")
                print(f"   Function: {event.function_call.name}")
                print(f"   Response: {event.function_response.response}")
                print("   ⏳ Waiting for human intervention...")
                # The LongRunningEvent is the last event.
                # BUT Please DON'T break this loop, let it naturally complete and let trace report correctly.
            elif event.content and event.content.parts and event.author != "user":
                if event.partial:
                    # Stream output
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    # Complete event
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [Calling tool: {part.function_call.name}]")
                            print(f"   Args: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [Tool result: {part.function_response.response}]")
    finally:
        await runner.close()

    return captured_long_running_event


async def run_agent():
    """Run the agent with support for long-running events"""

    print("🔧 LangGraph Human-In-The-Loop Demo")
    print("=" * 60)
    print("This demo shows how to handle human approval in LangGraph using interrupts.")
    print("=" * 60)

    # Create Agent and Session Service
    agent = create_agent()
    session_service = InMemorySessionService()

    # Create invocation parameters
    params = InvocationParams(
        user_id="demo_user",
        session_id=str(uuid.uuid4()),
        agent=agent,
        session_service=session_service,
        app_name="langgraph_human_in_loop_demo",
    )

    # Test query that will trigger human approval
    query = "I need to delete the production database 'user_data' for migration purposes. The details are: environment=prod, backup_created=true, reason=migration_to_new_system"

    print(f"\n📝 Query: {query}")
    print("🤖 Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=query)])

    # First run - will encounter long-running event
    long_running_event = await run_invocation(params, user_content)

    # Simulate human intervention
    if long_running_event:
        print("\n👤 Human intervention simulation...")
        await asyncio.sleep(2)  # Simulate human thinking time

        function_name = long_running_event.function_call.name
        response_data = long_running_event.function_response.response
        print(f"🤖 Assistant: {function_name}: {response_data}")

        # Simulate human decision (change to "rejected" to test rejection path)
        human_decision = "approved"  # or "rejected"
        print(f"   Human decision: {human_decision}")

        # Create the response data for resuming
        resume_data = {"status": human_decision}

        # Manually build resume content with FunctionResponse
        resume_function_response = FunctionResponse(
            id=long_running_event.function_response.id,
            name=long_running_event.function_response.name,
            response=resume_data,
        )
        resume_content = Content(role="user", parts=[Part(function_response=resume_function_response)])

        print("\n🔄 Resuming agent execution...")

        # Second run - resume with human input (creates a new runner)
        await run_invocation(params, resume_content)

    print("\n✅ LangGraph Human-In-The-Loop Demo completed!")


async def main():
    """Main function"""
    try:
        await run_agent()
    except KeyboardInterrupt:
        print("\n\n👋 Demo interrupted")
    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
