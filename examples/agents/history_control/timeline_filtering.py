#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TimelineFilterMode
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_agent(timeline_mode: TimelineFilterMode):
    """Create an agent with specific timeline filter mode.

    Args:
        timeline_mode: The timeline filter mode to use
    """
    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    return LlmAgent(
        name="timeline_demo",
        model=model,
        description="Agent demonstrating timeline filtering",
        instruction="""You are a helpful assistant.
When answering, reference any previous context you can see.
If you can see context from previous requests, mention it.""",
        # Key feature: Timeline-based filtering
        message_timeline_filter_mode=timeline_mode,
    )


async def demo_timeline_all():
    """Demonstrate TimelineFilterMode.ALL - sees all history."""

    print("\n" + "=" * 60)
    print("TimelineFilterMode.ALL Demo")
    print("=" * 60)
    print("Agent sees ALL historical messages across all requests\n")

    APP_NAME = "timeline_all_demo"
    USER_ID = "demo_user"
    SESSION_ID = str(uuid.uuid4())

    agent = create_agent(TimelineFilterMode.ALL)
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    # Multiple requests in same session
    requests = [
        "Request 1: My favorite color is blue.",
        "Request 2: I have a dog named Max.",
        "Request 3: What do you know about my preferences and pets?",
    ]

    for i, message in enumerate(requests, 1):
        print(f"\n--- Request {i} (new run_async call) ---")
        print(f"User: {message}")
        print("Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=message)])

        async for event in runner.run_async(user_id=USER_ID, session_id=SESSION_ID, new_message=user_content):
            if event.content and event.content.parts:
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    if not event.partial:
                        print()

    print("\n✓ Result: Agent can see ALL previous messages")
    print("=" * 60)


async def demo_timeline_invocation():
    """Demonstrate TimelineFilterMode.INVOCATION - only sees current invocation."""

    print("\n" + "=" * 60)
    print("TimelineFilterMode.INVOCATION Demo")
    print("=" * 60)
    print("Agent only sees messages from CURRENT invocation (runner.run_async() call)\n")

    APP_NAME = "timeline_invocation_demo"
    USER_ID = "demo_user"
    SESSION_ID = str(uuid.uuid4())

    agent = create_agent(TimelineFilterMode.INVOCATION)
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    # Multiple requests in same session
    requests = [
        "Request 1: My favorite color is blue.",
        "Request 2: I have a dog named Max.",
        "Request 3: What do you know about my preferences and pets?",
    ]

    for i, message in enumerate(requests, 1):
        print(f"\n--- Request {i} (new run_async call) ---")
        print(f"User: {message}")
        print("Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=message)])

        async for event in runner.run_async(
                user_id=USER_ID,
                session_id=SESSION_ID,
                new_message=user_content,
        ):
            if event.content and event.content.parts:
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    if not event.partial:
                        print()

    print("\n✓ Result: Agent CANNOT see messages from previous invocations")
    print("Each invocation is isolated - good for stateless APIs")
    print("=" * 60)


if __name__ == "__main__":
    print("\n⏰ Timeline Filtering Example")
    print("Demonstrates message_timeline_filter_mode parameter\n")

    # Run demos
    asyncio.run(demo_timeline_all())
    asyncio.run(demo_timeline_invocation())

    print("\n" + "=" * 60)
    print("Key Takeaways:")
    print("- TimelineFilterMode.ALL: Full conversation history")
    print("- TimelineFilterMode.INVOCATION: Invocation-scoped history (per runner.run_async() call)")
    print("- Choose based on your isolation requirements")
    print("=" * 60)
