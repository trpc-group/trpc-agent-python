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


async def run_weather_toolset_agent():
    """Run the weather ToolSet agent demo"""

    app_name = "weather_toolset_demo"

    from agent.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    test_scenarios = [
        {
            "user_id":
            "basic_user",
            "user_type":
            "basic",
            "queries": [
                "What's the current weather in Beijing?",
                "Get the weather forecast for Beijing for the next 5 days",
            ],
        },
        {
            "user_id": "vip_user",
            "user_type": "vip",
            "queries": [
                "Get the weather forecast for Beijing for the next 5 days",
            ],
        },
    ]

    for scenario in test_scenarios:
        user_id = scenario["user_id"]
        user_type = scenario["user_type"]
        session_id = str(uuid.uuid4())

        print(f"\n👤 User Type: {user_type.upper()}")
        print("=" * 40)

        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state={"user_type": user_type},
        )

        for i, query in enumerate(scenario["queries"], 1):
            print(f"\n📝 Test {i}: {query}")
            print("🤖 Assistant: ", end="", flush=True)

            user_content = Content(parts=[Part.from_text(text=query)])

            async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
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
                        print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                    elif part.function_response:
                        print(f"📊 [Tool Result: {part.function_response.response}]")

            print("\n" + "-" * 40)

    await runner.close()
    print("\n✅ Weather ToolSet demo finished!")


if __name__ == "__main__":
    asyncio.run(run_weather_toolset_agent())
