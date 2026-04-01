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


async def run_compose_agent():
    """Run the Combined Agent Demo"""

    APP_NAME = "compose_agent_demo"
    USER_ID = "demo_user"

    print("=" * 40)
    print("Compose Agent Demo - Combined Orchestration")
    print("=" * 40)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    test_content = """
    Smart Home Security System

    Our AI-powered security system provides 24/7 monitoring with facial recognition,
    motion detection, and mobile alerts. The system stores user data including video
    recordings and personal information for security analysis.

    Features:
    - Real-time monitoring
    - Mobile app notifications
    - Cloud storage for recordings
    - User data analytics
    """

    print("Analyze Content：")
    print(test_content.strip())
    print("\nRun Process：")

    user_message = Content(parts=[Part.from_text(text=test_content)])

    async for event in runner.run_async(user_id=USER_ID, session_id=str(uuid.uuid4()), new_message=user_message):
        if event.content and event.content.parts and event.author != "user":
            if not event.partial:
                for part in event.content.parts:
                    if part.text:
                        if event.author == "report_generator":
                            print("\n[Comprehensive Report]")
                            print(part.text)
                        else:
                            print(f"[{event.author}] {part.text}")
                            print("-" * 30)


if __name__ == "__main__":
    asyncio.run(run_compose_agent())
