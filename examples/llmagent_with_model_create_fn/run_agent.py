# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_weather_agent():
    """Run the weather query agent demo with model factory."""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()

    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    query = "What's the weather like in Beijing today?"

    print(f"📝 User: {query}")
    print("🤖 Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=query)])

    # Pass RunConfig with custom_data to run_async
    run_config = RunConfig(custom_data={
        "user_tier": "premium",
    })

    async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
            run_config=run_config  # Pass RunConfig here
    ):
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
                print(f"\n🔧 [Tool: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                print(f"📊 [Result: {part.function_response.response}]")

    print("\n")


if __name__ == "__main__":
    asyncio.run(run_weather_agent())
