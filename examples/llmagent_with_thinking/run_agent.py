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


async def run_weather_agent():
    """Run the weather query agent demo to demonstrate the various capabilities of an LLM agent."""

    app_name = "weather_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "What's the weather like today?",
        "What's the current weather in Guangzhou?",
        "Please check both the current weather in Guangzhou and the three-day weather forecast for Shanghai.",
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

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

        printed_thinking = False
        printed_assistant = False
        in_thinking = False
        thinking_line_start = False
        assistant_text_started = False

        def print_assistant_header() -> None:
            nonlocal printed_assistant
            if printed_assistant:
                return
            if printed_thinking:
                print("\n")
            print("🤖 Assistant: ", end="", flush=True)
            printed_assistant = True

        def print_thinking_header() -> None:
            nonlocal in_thinking, printed_thinking, thinking_line_start
            if in_thinking:
                return
            print("\n  💭 Thinking: ", end="", flush=True)
            in_thinking = True
            printed_thinking = True
            thinking_line_start = False

        def print_thinking_text(text: str) -> None:
            nonlocal thinking_line_start
            for line in text.splitlines(keepends=True):
                if thinking_line_start:
                    print("  ", end="", flush=True)
                print(line, end="", flush=True)
                thinking_line_start = line.endswith("\n")

        def close_thinking_section() -> None:
            nonlocal in_thinking, thinking_line_start
            if in_thinking:
                if not thinking_line_start:
                    print()
                print("  💭 End Thinking")
                in_thinking = False
                thinking_line_start = False

        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        if part.thought:
                            if assistant_text_started:
                                continue
                            print_thinking_header()
                            print_thinking_text(part.text)
                        else:
                            close_thinking_section()
                            print_assistant_header()
                            assistant_text_started = True
                            print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought and part.text and not printed_thinking and not assistant_text_started:
                    print_thinking_header()
                    print_thinking_text(part.text)
                elif part.function_call:
                    close_thinking_section()
                    print_assistant_header()
                    print(f"\n🔧 [Invoke Tool:: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    close_thinking_section()
                    printed_thinking = False
                    print_assistant_header()
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_weather_agent())
