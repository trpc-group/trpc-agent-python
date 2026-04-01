#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.events import LongRunningEvent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import LongRunningFunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


async def human_approval_required(task_description: str, details: dict) -> dict:
    """A long-running function that requires human approval.

    Args:
        task_description: Description of the task requiring approval
        details: Additional details about the task

    Returns:
        A dictionary indicating the task is pending human approval
    """
    return {
        "status": "pending_approval",
        "message": f"Task '{task_description}' requires human approval",
        "details": details,
        "approval_id": str(uuid.uuid4()),
        "timestamp": time.time(),
    }


async def check_system_critical_operation(operation: str, target: str) -> dict:
    """A long-running function for sub-agent that requires human approval for critical operations.

    Args:
        operation: The critical operation to perform (e.g., delete, restart, update)
        target: The target of the operation (e.g., server name, database name)

    Returns:
        A dictionary indicating the operation requires human approval
    """
    return {
        "status": "pending_approval",
        "message": f"Critical operation '{operation}' on '{target}' requires human approval",
        "operation": operation,
        "target": target,
        "approval_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "risk_level": "high",
    }


@dataclass
class InvocationParams:
    """Parameters for running an invocation"""

    user_id: str
    session_id: str
    agent: LlmAgent
    session_service: InMemorySessionService
    app_name: str


async def run_invocation(
    params: InvocationParams,
    content: Content,
) -> Optional[LongRunningEvent]:
    """Run an invocation with a fresh runner instance.

    Args:
        params: Invocation parameters containing user_id, session_id, agent, and session_service
        content: The content to send to the agent
        long_running_event: Optional long-running event to resume from

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
                            print(f"\n🔧 [{event.author}][Calling tool: {part.function_call.name}]")
                            print(f"   Args: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [{event.author}][Tool result: {part.function_response.response}]\n")
                        elif part.text:
                            print(f"\n💬 [{event.author}]{part.text}")
    finally:
        await runner.close()

    return captured_long_running_event


def create_agent():
    """Create an agent configured with Long Running Function Tools and Sub-Agents"""

    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # Create long-running tool for main agent
    approval_tool = LongRunningFunctionTool(human_approval_required)

    # Create long-running tool for sub-agent
    critical_operation_tool = LongRunningFunctionTool(check_system_critical_operation)

    # Create a sub-agent that handles critical system operations with human approval
    system_operations_agent = LlmAgent(
        name="system_operations_agent",
        model=model,
        description="System operations specialist that handles critical operations requiring human approval",
        instruction="""You are a system operations specialist.
When asked to perform critical operations like deleting, restarting, or updating systems,
use the check_system_critical_operation tool to request human approval.
Always specify the operation type and target clearly.""",
        tools=[critical_operation_tool],
        disallow_transfer_to_parent=True,
        output_key="system_ops_result",
    )

    # Main coordinator agent with sub-agent
    return LlmAgent(
        name="human_in_loop_agent",
        description="Agent demonstrating long-running tools with human-in-the-loop and sub-agents",
        model=model,
        instruction="""You are an assistant that can handle long-running operations requiring human approval.
When you encounter tasks that need approval, use the appropriate tool and wait for human intervention.
For system-related critical operations, transfer to the system_operations_agent.""",
        tools=[approval_tool],
        sub_agents=[system_operations_agent],
    )


async def run_agent():
    """Run the agent with support for long-running events"""

    print("🔧 Long Running Tools Demo with Sub-Agents")
    print("=" * 60)
    print("This demo shows how to handle long-running operations that require human intervention,")
    print("including sub-agents that can raise human-in-the-loop events.")
    print("=" * 60)

    # Create Agent and Session Service
    agent = create_agent()
    session_service = InMemorySessionService()

    # Test scenarios
    test_scenarios = [
        {
            "title": "Main Agent - Database Deletion Approval",
            "query":
            "I need approval to delete the production database. The details are: environment=prod, database=user_data, reason=migration",
            "session_id": str(uuid.uuid4()),
            "approval_message": "APPROVED: The database deletion is approved for migration purposes.",
        },
        {
            "title": "Sub-Agent - Critical System Operation",
            "query": "I need to restart the production server 'web-server-01' for maintenance",
            "session_id": str(uuid.uuid4()),
            "approval_message": "APPROVED: Server restart is approved for scheduled maintenance.",
        },
    ]

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n{'=' * 60}")
        print(f"Scenario {i}: {scenario['title']}")
        print(f"{'=' * 60}")

        # Create invocation parameters for this scenario
        params = InvocationParams(
            user_id="demo_user",
            session_id=scenario["session_id"],
            agent=agent,
            session_service=session_service,
            app_name="agent_demo",
        )

        print(f"\n📝 Query: {scenario['query']}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=scenario["query"])])

        # First run - will encounter long-running event
        long_running_event = await run_invocation(params, user_content)

        # Simulate human intervention
        if long_running_event:
            print("\n👤 Human intervention simulation...")
            await asyncio.sleep(2)  # Simulate human thinking time

            # Simulate human providing input for approval
            function_name = long_running_event.function_call.name
            response_data = long_running_event.function_response.response
            print(f"🤖 Assistant: {function_name}: {response_data}")
            if response_data["status"] != "pending_approval":
                print("   ❌ Invalid response status")
                continue

            response_data["status"] = "approved"
            response_data["message"] = scenario["approval_message"]
            response_data["approved_by"] = "admin"
            response_data["timestamp"] = time.time()

            print(f"   Human response: {response_data}")

            # Manually build resume content with FunctionResponse
            resume_function_response = FunctionResponse(
                id=long_running_event.function_response.id,
                name=long_running_event.function_response.name,
                response=response_data,
            )
            resume_content = Content(role="user", parts=[Part(function_response=resume_function_response)])

            print("\n🔄 Resuming agent execution...")

            # Second run - resume with human input (creates a new runner)
            await run_invocation(params, resume_content)

    print(f"\n{'=' * 60}")
    print("✅ Long Running Tools Demo with Sub-Agents completed!")
    print(f"{'=' * 60}")


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
