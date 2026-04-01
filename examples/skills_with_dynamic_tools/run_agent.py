#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Example demonstrating the skills run flow in TRPC Agent framework.
"""
import asyncio
import os
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_skill_run_demo():
    """Run the skill run agent demo to demonstrate the various capabilities of an LLM agent."""

    app_name = "skill_run_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    weather_query = """
        Please load the weather-tools skill first.
        First, what's the current weather in Beijing?
        Second, can you get me a 3-day forecast for Shanghai?
        Then, can you search for information about New York city?
        I need to know its coordinates and timezone.
        Finally, can you ask for information about Alice in China?
    """

    demo_queries = [
        weather_query,
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
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
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool:: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    os.system("rm -rf /tmp/ws_*")
    os.system("echo 'hello from skillrun' > /tmp/skillrun-notes.txt")
    os.system("echo 'this is another line' >> /tmp/skillrun-notes.txt")
    # Create sample CSV file for data analysis skill
    os.system("""cat > /tmp/sales_data.csv << 'EOF'
Date,Product,Sales,Quantity,Region
2024-01-01,Product A,1000,10,North
2024-01-02,Product B,1500,15,South
2024-01-03,Product A,1200,12,North
2024-01-04,Product C,800,8,East
2024-01-05,Product B,2000,20,South
2024-01-06,Product A,900,9,West
2024-01-07,Product C,1100,11,East
2024-01-08,Product B,1800,18,North
EOF
""")
    asyncio.run(run_skill_run_demo())
