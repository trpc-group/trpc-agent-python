# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Steering demo for a single process.

The user first asks for Beijing. The weather tool call takes one second. After
it returns, a second Runner sends a steering message correcting the request to
Shanghai. The message is consumed before the next LLM request, so the model
reacts to the correction in the same run.

Run with:

    python3 run_agent.py
"""

import asyncio
import time
import uuid

from dotenv import load_dotenv

from trpc_agent_sdk.cancel import SteeringStatus
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "weather_agent_steering_demo"
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

            for response in event.get_function_responses():
                response_key = (response.id, response.name)
                if response_key in seen_responses:
                    continue
                seen_responses.add(response_key)
                print(f"\n[{_elapsed(start)}] [function response] {response.name}: {response.response}")
                # The first tool call has finished. Send the correction now:
                # the agent drains steering before building its next LLM request.
                if steering_status is None:
                    steering_status = await steering_runner.send_steering(
                        user_id=USER_ID,
                        session_id=session_id,
                        content=Content(role="user", parts=[Part.from_text(text=STEERING_TEXT)]),
                    )
                    print(f"[{_elapsed(start)}] [steering] sent after first tool call: "
                          f"status={steering_status} text={STEERING_TEXT!r}")
    finally:
        await runner.close()
        await steering_runner.close()

    print()
    print(f"function call cities: {cities}")
    print(f"final answer: {''.join(final_parts)}")

    session = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    print("session events:")
    for index, event in enumerate(session.events if session else []):
        calls = [f"{call.name}({call.args})" for call in event.get_function_calls()]
        responses = [f"{response.name}->{response.response}" for response in event.get_function_responses()]
        print(f"  [{index}] author={event.author} visible={event.visible} "
              f"text={event.get_text()!r} calls={calls} responses={responses}")

    if steering_status is not SteeringStatus.ACCEPTED:
        raise RuntimeError(f"steering was not accepted: {steering_status}")
    if "Shanghai" not in cities:
        raise RuntimeError("steering was not applied: no Shanghai tool call in the same run")
    if cities.index("Shanghai") == 0:
        raise RuntimeError("no weather tool call happened before the steering message")

    print("steering verified")


if __name__ == "__main__":
    asyncio.run(main())
