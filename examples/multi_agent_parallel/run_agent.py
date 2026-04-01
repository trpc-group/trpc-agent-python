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

load_dotenv()


async def run_parallel_agent():
    """Run the Parallel Agent Demo"""

    APP_NAME = "parallel_agent_demo"
    USER_ID = "demo_user"

    print("=" * 40)
    print("Parallel Agent Demo - Parallel Review")
    print("=" * 40)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    test_content = """
    AI Smart Home System - Our system collects user data including personal
    preferences and usage patterns. Data is stored in cloud servers with
    basic encryption. Users can access the system through mobile apps.
    """

    print("Review Content:")
    print(test_content.strip())
    print("\nParallel Reviewing:")

    user_message = Content(parts=[Part.from_text(text=test_content)])

    session_id = str(uuid.uuid4())

    async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(f"[{event.author}] Finished")

    # Get the Parallel Review Results
    session = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)

    print("\nReview Result:")
    print("-" * 30)

    # Display the review results of each Agent
    if session and session.state:
        if "quality_review" in session.state:
            print("\n[Quality Review]")
            print(session.state["quality_review"])

        if "security_review" in session.state:
            print("\n[Security Review]")
            print(session.state["security_review"])


if __name__ == "__main__":
    asyncio.run(run_parallel_agent())
