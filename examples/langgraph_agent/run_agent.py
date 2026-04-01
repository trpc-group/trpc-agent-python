# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
LangGraph Calculator Agent Demo

This example demonstrates basic LangGraphAgent usage with trpc_agent_sdk framework,
showing how to:
1. Create a LangGraph with tool calling
2. Run the agent with Runner
3. Handle streaming events
"""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
) -> None:
    """Run agent with a single query and handle events.

    Args:
        runner: The runner instance
        user_id: User identifier
        session_id: Session identifier
        query: User query text
    """
    user_content = Content(parts=[Part.from_text(text=query)])

    print("Assistant: ", end="", flush=True)
    try:
        run_config = RunConfig()
        # run_config = RunConfig(agent_run_config={
        #     "subgraphs": True,
        # })
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
            run_config=run_config,
        ):
            if not event.content or not event.content.parts:
                continue

            # Print streaming text
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # Print tool calls and responses
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n[Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"[Tool Result: {part.function_response.response}]")

            # # Print final text response
            # for part in event.content.parts:
            #     if part.text and not event.partial:
            #         print(part.text, end="", flush=True)

    except Exception as e:
        print(f"\nError: {e}")

    print()  # New line after response


def create_runner(app_name: str, session_service: InMemorySessionService) -> Runner:
    """Create a new runner instance.

    Args:
        app_name: Application name
        session_service: Session service instance

    Returns:
        A new Runner instance
    """
    from agent.agent import root_agent
    return Runner(app_name=app_name, agent=root_agent, session_service=session_service)


async def run_demo() -> None:
    """Run LangGraph agent demo with multiple queries."""

    print("=" * 60)
    print("LangGraph Agent Demo")
    print("=" * 60)
    print()

    # Common settings
    app_name = "langgraph_demo"
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    # Create session service
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    # Create runner
    runner = create_runner(app_name, session_service)

    # Demo queries
    queries = [
        "Hello, who are you?",
        "Please calculate 15 multiply 23.",
        "Now divide the result by 5.",
        "Thank you!",
    ]

    for query in queries:
        print(f"User: {query}")
        await run_agent(runner, user_id, session_id, query)
        print()

    print("=" * 60)
    print("Demo completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_demo())
