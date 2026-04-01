#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import time
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_agent():
    """Run HobbyToolSet Agent Demo"""

    print("=" * 60)
    print("🔧 HobbyToolSet Agent Demo")
    print("=" * 60)

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name="hobby_toolset_demo", agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "Alice 喜欢边运动边看电视并且同时听音乐，请分析一下她运行看电视和听音乐相关的内容",
    ]

    start = time.time()
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
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)
    print(f"cost {time.time() - start}")

    # Need to actively close here
    await runner.close()
    print("\n✅ HobbyToolSet Agent Demo End！")


async def main():
    try:
        await run_agent()
    except KeyboardInterrupt:
        print("\n\n👋 The demo was interrupted")
    except Exception as e:
        print(f"\n❌ An error occurred: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
