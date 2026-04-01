#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_agent_with_history_limit(max_runs: int = 0):
    """Create an agent with history limit.

    Args:
        max_runs: Maximum number of history messages to include.
                  0 means no limit (default behavior).
    """
    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    return LlmAgent(
        name="history_demo",
        model=model,
        description="Agent demonstrating history control",
        instruction="""You are a helpful assistant that remembers conversation context.
When answering questions, reference previous messages if relevant.
If you can see previous context, mention it. If you can't, note that as well.""",
        # Key feature: Limit conversation history
        max_history_messages=max_runs,
    )


async def run_conversation_demo():
    """Run a multi-turn conversation to demonstrate history control."""

    APP_NAME = "history_basic_demo"
    USER_ID = "demo_user"

    print("=" * 60)
    print("Basic History Control Demo - max_history_messages")
    print("=" * 60)

    # Test with different history limits
    test_scenarios = [
        {
            "title": "Scenario 1: No History Limit (max_history_messages=0)",
            "max_runs": 0,
            "description": "Agent sees all previous messages"
        },
        {
            "title": "Scenario 2: Limited History (max_history_messages=2)",
            "max_runs": 2,
            "description": "Agent only sees last 2 messages"
        },
    ]

    # Conversation sequence
    conversation = [
        "My name is Alice.",
        "I work as a software engineer.",
        "I enjoy playing piano in my free time.",
        "What do you know about me?",  # This tests if agent remembers previous messages
    ]

    for scenario in test_scenarios:
        print(f"\n{scenario['title']}")
        print(f"Description: {scenario['description']}")
        print("-" * 60)

        # Create agent with specific history limit
        agent = create_agent_with_history_limit(max_runs=scenario["max_runs"])

        # Create fresh session for this scenario
        session_service = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

        scenario_session_id = str(uuid.uuid4())

        # Run through conversation
        for i, message in enumerate(conversation, 1):
            print(f"\nTurn {i}:")
            print(f"User: {message}")
            print("Assistant: ", end="", flush=True)

            user_content = Content(parts=[Part.from_text(text=message)])

            async for event in runner.run_async(user_id=USER_ID,
                                                session_id=scenario_session_id,
                                                new_message=user_content):
                if event.content and event.content.parts:
                    if event.partial:
                        for part in event.content.parts:
                            if part.text:
                                print(part.text, end="", flush=True)
                    else:
                        # Final response
                        if not event.partial:
                            print()  # New line after streaming

        print("\n" + "=" * 60)


if __name__ == "__main__":
    print("\n📚 Basic History Control Example")
    print("Demonstrates max_history_messages parameter\n")

    asyncio.run(run_conversation_demo())

    print("\n" + "=" * 60)
    print("Key Takeaways:")
    print("- max_history_messages=0 (default): No limit on history")
    print("- max_history_messages=N: Only last N messages included")
    print("- Use this to control token usage in long conversations")
    print("- Applied AFTER other filters (timeline, branch, etc.)")
    print("=" * 60)
