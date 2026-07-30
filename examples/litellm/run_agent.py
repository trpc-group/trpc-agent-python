# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run LiteLLM Agent with Runner + run_async (streaming). Env vars: see README."""

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

MODELS_TO_RUN = [
    "openai/gpt-5.1",
    "openai/claude-4-5-sonnet-20250929",
    "openai/gemini-3-pro",
    "openai/glm-4.7",
    "openai/qwen3-32b-fp8",
    "openai/deepseek-v3.1-terminus",
    "openai/kimi-k2.5",
]


async def run_one_query(agent, query: str) -> None:
    app_name = "litellm_demo"
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)
    user_id = "user1"
    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={
            "user_name": user_id,
            "user_city": "Beijing"
        },
    )
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
                print(f"\n🔧 [Invoke Tool:: {part.function_call.name}({part.function_call.args})]")
            elif part.function_response:
                print(f"📊 [Tool Result: {part.function_response.response}]")
    print()


async def main():
    from agent.agent import create_agent

    query = "What will the weather be like in Shanghai for the next three days?"
    for model_name in MODELS_TO_RUN:
        print("=" * 60)
        print(f"Model: {model_name}")
        print("=" * 60)
        print(f"User: {query}\nAssistant: ", end="", flush=True)
        try:
            agent = create_agent(model_name=model_name)
            await run_one_query(agent, query)
        except Exception as e:
            print(f"[FAIL] {e}")
        print("-" * 40)


if __name__ == "__main__":
    asyncio.run(main())
