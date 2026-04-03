# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_chain_agent():
    """Run the chained Agent demo."""

    # App and user identifiers
    APP_NAME = "chain_agent_demo"
    USER_ID = "demo_user"

    print("=" * 60)
    print("Chain Agent Demo - Information Passing via output_key")
    print("=" * 60)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    # test text
    test_content = """
    Product Name: Smart Home Control System.
    Main Features: Voice Control, Remote Monitoring, Automated Scene Setting.
    Technical Features: Support for multiple device connections, AI intelligent learning of user habits, cloud data synchronization.
    Target Users: Modern families pursuing a convenient lifestyle, technology enthusiasts.
    Price: Starting from CNY 999.
    """

    print(f"Input content: {test_content}")
    print("\nProcessing Flow: Extraction → Translation")

    user_message = Content(parts=[Part.from_text(text=test_content)])

    async for event in runner.run_async(user_id=USER_ID, session_id=str(uuid.uuid4()), new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(f"\n[{event.author}] Output:")
                        print(part.text)
                        print("-" * 40)


if __name__ == "__main__":
    asyncio.run(run_chain_agent())
