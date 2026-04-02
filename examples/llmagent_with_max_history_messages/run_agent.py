# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


async def run_history_demo():
    """Run a multi-turn conversation to demonstrate history control."""

    app_name = "history_basic_demo"
    user_id = "demo_user"

    from agent.agent import create_agent

    # Test with different history limits
    test_scenarios = [
        {
            "title": "Scenario 1: No History Limit (max_history_messages=0)",
            "max_history_messages": 0,
            "description": "Agent sees all previous messages"
        },
        {
            "title": "Scenario 2: Limited History (max_history_messages=2)",
            "max_history_messages": 2,
            "description": "Agent only sees last 2 messages"
        },
    ]

    # Conversation sequence
    demo_queries = [
        "My name is Alice.",
        "I work as a software engineer.",
        "I enjoy playing piano in my free time.",
        "What do you know about me?",
    ]

    for scenario in test_scenarios:
        print("=" * 60)
        print(f"{scenario['title']}")
        print(f"Description: {scenario['description']}")
        print("=" * 60)

        agent = create_agent(max_history_messages=scenario["max_history_messages"])

        session_service = InMemorySessionService()
        runner = Runner(app_name=app_name, agent=agent, session_service=session_service)

        current_session_id = str(uuid.uuid4())

        for i, query in enumerate(demo_queries, 1):
            print(f"\nTurn {i}:")
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


if __name__ == "__main__":
    asyncio.run(run_history_demo())
