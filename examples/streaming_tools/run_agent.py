# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.models import constants as const
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_streaming_tool_agent():
    """Run the streaming tool demo agent"""

    app_name = "streaming_tool_demo"

    from agent.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "请帮我创建一个 Python 脚本 hello.py，实现简单的计算器功能",
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=current_session_id,
        )

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")
        print("🤖 Assistant: ", end="", flush=True)

        user_content = Content(parts=[Part.from_text(text=query)])

        accumulated_content = ""

        async for event in runner.run_async(
                user_id=user_id,
                session_id=current_session_id,
                new_message=user_content,
        ):
            if not event.content or not event.content.parts:
                continue

            if event.is_streaming_tool_call():
                for part in event.content.parts:
                    if part.function_call:
                        delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                        accumulated_content += delta
                        print(f"⏳ Generated {len(accumulated_content)} chars...", end="\r")
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
                    print(f"\n✅ Code generation complete!")
                    accumulated_content = ""
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                elif part.text:
                    print(f"\n💬 {part.text}")

        print("\n" + "-" * 40)

    await runner.close()


if __name__ == "__main__":
    asyncio.run(run_streaming_tool_agent())
