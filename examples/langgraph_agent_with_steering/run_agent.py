# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Steering demo for LangGraphAgent.

The first query asks for Beijing weather. Once the model emits its first tool
call, a second Runner sends a steering message that corrects the city to
Shanghai. The graph runs ``steering_node`` after the tool node, so the next
chatbot call sees the correction in the same run.

Run with:

    python3 run_agent.py
"""

import asyncio
import time
import uuid

from dotenv import load_dotenv

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "langgraph_agent_steering_demo"
USER_ID = "demo_user"
QUERY = "What's the current weather in Beijing?"
STEERING_TEXT = "Sorry, I said it wrong. Please check Shanghai's weather instead."


def _create_runner(session_service: InMemorySessionService) -> Runner:
    from agent.agent import root_agent
    return Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)


def _elapsed(start: float) -> str:
    return f"{time.monotonic() - start:.2f}s"


async def main() -> None:
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()
    runner = _create_runner(session_service)
    steering_runner = _create_runner(session_service)

    start = time.monotonic()
    steering_status = None
    cities = []
    seen_calls = set()
    seen_responses = set()
    final_parts = []

    print(f"session={session_id}")
    print(f"user: {QUERY}")
    print("assistant: ", end="", flush=True)

    try:
        async for event in runner.run_async(
                user_id=USER_ID,
                session_id=session_id,
                new_message=Content(parts=[Part.from_text(text=QUERY)]),
        ):
            if event.partial and event.content:
                for part in event.content.parts:
                    if part.text and not part.thought:
                        print(part.text, end="", flush=True)
            elif event.content:
                for part in event.content.parts:
                    if part.text and not part.thought:
                        final_parts.append(part.text)

            for call in event.get_function_calls():
                call_key = (call.id, call.name, str(call.args))
                if call_key in seen_calls:
                    continue
                seen_calls.add(call_key)
                city = (call.args or {}).get("city")
                print(f"\n[{_elapsed(start)}] [function call] {call.name}({call.args})")
                if city:
                    cities.append(city)

                # Send steering before the tool node runs. The steering node
                # consumes it after the tool result and before the next chatbot
                # call.
                if steering_status is None:
                    steering_status = await steering_runner.send_steering(
                        user_id=USER_ID,
                        session_id=session_id,
                        content=Content(role="user", parts=[Part.from_text(text=STEERING_TEXT)]),
                    )
                    print(f"[{_elapsed(start)}] [steering] status={steering_status} "
                          f"text={STEERING_TEXT!r}")

            for response in event.get_function_responses():
                response_key = (response.id, response.name)
                if response_key in seen_responses:
                    continue
                seen_responses.add(response_key)
                print(f"\n[{_elapsed(start)}] [function response] {response.name}: {response.response}")
    finally:
        await runner.close()
        await steering_runner.close()

    print()
    print(f"function call cities: {cities}")
    print(f"final answer: {''.join(final_parts)}")

    if steering_status is None:
        raise RuntimeError("steering was not sent")
    if not steering_status.ok:
        raise RuntimeError(f"steering was not accepted: {steering_status.error_msg}")
    if cities[:2] != ["Beijing", "Shanghai"]:
        raise RuntimeError(f"steering was not applied: tool call cities={cities}")

    print("steering verified")


if __name__ == "__main__":
    asyncio.run(main())
