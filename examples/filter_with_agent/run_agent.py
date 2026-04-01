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


async def run_weather_agent():
    """Run the weather query agent demo"""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # Demonstrate query list
    demo_queries = [
        "What's the weather like today?",
        "What's the current weather in Beijing?",
    ]

    for query in demo_queries:
        # Use a new session for each query
        current_session_id = str(uuid.uuid4())

        # Create state variables for a new session
        # If session management is not required, there is no need to use the session_service,
        # the trpc_agent_sdk will create a session automatically
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=current_session_id,
            state={
                "user_name": f"{user_id}",
                "user_city": "Beijing"
            },
        )

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            # Check if event.content exists
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                # Skip the reasoning part; the output is already generated when partial=True
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # Uncomment to get the full text output of the LLM
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_weather_agent())
