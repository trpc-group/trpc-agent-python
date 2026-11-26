# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Steering demo for ClaudeAgent.

The first query asks for Beijing weather. Once the model emits its first tool
call, a second Runner sends a steering message that corrects the city to
Shanghai. Claude receives the steering after the running tool finishes and
continues the same turn with the corrected city.

Run with:

    python3 run_agent.py
"""

import asyncio
import time
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

APP_NAME = "claude_agent_steering_demo"
USER_ID = "demo_user"
QUERY = "What's the current weather in Beijing? Use the weather tool and answer with the result."
STEERING_TEXT = ("Sorry, I said it wrong. Please check Shanghai's weather instead. "
                 "Use the weather tool for Shanghai and answer with that result.")


def _elapsed(start: float) -> str:
    return f"{time.monotonic() - start:.2f}s"


def _create_runner(session_service: InMemorySessionService) -> Runner:
    from agent.agent import root_agent
    return Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)


async def main() -> None:
    from agent.agent import _create_model
    from agent.agent import root_agent

    model = _create_model()
    setup_claude_env(
        proxy_host="127.0.0.1",
        proxy_port=8082,
        claude_models={"all": model},
    )
    root_agent.initialize()

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
    print(f"enable_session={root_agent.enable_session}")
    print(f"user: {QUERY}")
    print("assistant: ", end="", flush=True)

    try:
        async for event in runner.run_async(
                user_id=USER_ID,
                session_id=session_id,
                new_message=Content(parts=[Part.from_text(text=QUERY)]),
        ):
            if not getattr(event, "visible", True):
                continue

            if event.partial and event.content:
                for part in event.content.parts:
                    if part.text and not part.thought:
                        print(part.text, end="", flush=True)
            elif event.content:
                for part in event.content.parts:
                    if part.text and not part.thought:
                        final_parts.append(part.text)

            for call in event.get_function_calls():
                args = call.args or {}
                if "tool_streaming_args" in args and "city" not in args:
                    continue

                call_key = (call.id, call.name, str(call.args))
                if call_key in seen_calls:
                    continue
                seen_calls.add(call_key)

                city = (call.args or {}).get("city")
                print(f"\n[{_elapsed(start)}] [function call] {call.name}({call.args})")
                if city:
                    cities.append(city)

                # Send steering while the first weather tool is running. The
                # next model request in the same Claude turn sees this message.
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
        root_agent.destroy()
        destroy_claude_env()

    final_text = "".join(final_parts)
    print()
    print(f"function call cities: {cities}")
    print(f"final answer: {final_text}")

    if steering_status is None:
        raise RuntimeError("steering was not sent")
    if not steering_status.ok:
        raise RuntimeError(f"steering was not accepted: {steering_status.error_msg}")
    if cities[:2] != ["Beijing", "Shanghai"]:
        raise RuntimeError(f"steering was not applied: tool call cities={cities}")
    if not final_text.strip():
        raise RuntimeError("agent returned an empty final answer")

    print("steering verified")


if __name__ == "__main__":
    asyncio.run(main())
