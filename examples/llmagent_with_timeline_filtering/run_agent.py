# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.agents import TimelineFilterMode
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


async def run_timeline_demo():
    """Run demos to demonstrate timeline filtering modes."""

    app_name = "timeline_filter_demo"
    user_id = "demo_user"

    from agent.agent import create_agent

    # Test with different timeline filter modes
    test_scenarios = [
        {
            "title": "Scenario 1: TimelineFilterMode.ALL",
            "timeline_mode": TimelineFilterMode.ALL,
            "description": "Agent sees ALL historical messages across all requests",
        },
        {
            "title": "Scenario 2: TimelineFilterMode.INVOCATION",
            "timeline_mode": TimelineFilterMode.INVOCATION,
            "description": "Agent only sees messages from CURRENT invocation (runner.run_async() call)",
        },
    ]

    # Multiple requests in same session
    demo_queries = [
        "Request 1: My favorite color is blue.",
        "Request 2: I have a dog named Max.",
        "Request 3: What do you know about my preferences and pets?",
    ]

    for scenario in test_scenarios:
        print("=" * 60)
        print(f"{scenario['title']}")
        print(f"Description: {scenario['description']}")
        print("=" * 60)

        agent = create_agent(timeline_mode=scenario["timeline_mode"])

        session_service = InMemorySessionService()
        runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

        current_session_id = str(uuid.uuid4())

        for i, query in enumerate(demo_queries, 1):
            print(f"\n--- Request {i} (new run_async call) ---")
            print(f"📝 User: {query}")
            print("🤖 Assistant: ", end="", flush=True)

            user_content = Content(parts=[Part.from_text(text=query)])

            async for event in runner.run_async(user_id=user_id,
                                                session_id=current_session_id,
                                                new_message=user_content):
                if not event.content or not event.content.parts:
                    continue

                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                    continue

                for part in event.content.parts:
                    if part.thought:
                        continue

            print("\n" + "-" * 40)

        print()

    print("=" * 60)
    print("Key Takeaways:")
    print("- TimelineFilterMode.ALL: Full conversation history")
    print("- TimelineFilterMode.INVOCATION: Invocation-scoped history (per runner.run_async() call)")
    print("- Choose based on your isolation requirements")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_timeline_demo())
