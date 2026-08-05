# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run low-cost checks for each Agent run limit."""

import asyncio
import uuid
from dataclasses import dataclass

from dotenv import load_dotenv
from trpc_agent_sdk.configs import AgentRunLimits
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunLimitException
from trpc_agent_sdk.exceptions import RunLimitType
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

_CONTINUATION_QUERY = "What did we do previously?"


@dataclass(frozen=True)
class LimitScenario:
    """Configuration for one run-limit check."""

    name: str
    trigger_query: str
    limits: AgentRunLimits
    expected_limit: RunLimitType


def _create_limit_scenarios() -> list[LimitScenario]:
    """Create one low-cost scenario for each supported limit."""
    return [
        LimitScenario(
            name="max_llm_calls",
            trigger_query=("Use get_weather_report to check the current weather in Beijing."),
            limits=AgentRunLimits(
                max_llm_calls=1,
                max_iterations=0,
                max_tool_calls=0,
            ),
            expected_limit=RunLimitType.MAX_LLM_CALLS,
        ),
        LimitScenario(
            name="max_iterations",
            trigger_query=("Use get_weather_report to check the current weather in Beijing."),
            limits=AgentRunLimits(
                max_llm_calls=0,
                max_iterations=1,
                max_tool_calls=0,
            ),
            expected_limit=RunLimitType.MAX_ITERATIONS,
        ),
        LimitScenario(
            name="max_tool_calls",
            trigger_query=("For this message only, call both tools in the same response: "
                           "get_weather_report for Beijing and get_weather_forecast for "
                           "Beijing with days=1. Do not retry these calls in a later message."),
            limits=AgentRunLimits(
                max_llm_calls=0,
                max_iterations=0,
                max_tool_calls=1,
            ),
            expected_limit=RunLimitType.MAX_TOOL_CALLS,
        ),
    ]


def _print_event(event: Event) -> None:
    """Print visible content from an Agent event."""
    if not event.content or not event.content.parts:
        return

    if event.partial:
        for part in event.content.parts:
            if part.text:
                print(part.text, end="", flush=True)
        return

    for part in event.content.parts:
        if part.thought:
            continue
        if part.function_call:
            print(f"\n🔧 [Invoke Tool:: "
                  f"{part.function_call.name}({part.function_call.args})]")
        elif part.function_response:
            print(f"📊 [Tool Result: {part.function_response.response}]")


async def _run_invocation(
    runner: Runner,
    user_id: str,
    session_id: str,
    query: str,
    run_config: RunConfig,
) -> bool:
    """Run one prompt and return whether it produced a final response."""
    final_response_received = False
    user_content = Content(parts=[Part.from_text(text=query)])
    async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=user_content,
            run_config=run_config,
    ):
        _print_event(event)
        if event.is_final_response():
            final_response_received = True
    return final_response_received


async def run_weather_agent() -> None:
    """Run one isolated check for each supported run limit."""
    from agent.agent import root_agent

    app_name = "weather_agent_limit_demo"
    user_id = "demo_user"
    session_service = InMemorySessionService()
    runner = Runner(
        app_name=app_name,
        agent=root_agent,
        session_service=session_service,
    )

    scenarios = _create_limit_scenarios()
    for index, scenario in enumerate(scenarios, 1):
        session_id = str(uuid.uuid4())
        run_config = RunConfig(agent_limits={
            root_agent.name: scenario.limits,
        }, )
        continuation_run_config = RunConfig(agent_limits={
            root_agent.name:
            AgentRunLimits(
                max_llm_calls=0,
                max_iterations=0,
                max_tool_calls=0,
            ),
        }, )

        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            state={
                "user_name": user_id,
                "user_city": "Beijing",
            },
        )

        print(f"\n=== Scenario {index}/{len(scenarios)}: {scenario.name} ===")
        print(f"⚙️  Limits: {scenario.limits.model_dump()}")
        print(f"🆔 Session ID: {session_id[:8]}...")

        print("\n--- Invocation 1/2: trigger the limit ---")
        print(f"📝 User: {scenario.trigger_query}")
        print("🤖 Assistant: ", end="", flush=True)
        try:
            await _run_invocation(
                runner,
                user_id,
                session_id,
                scenario.trigger_query,
                run_config,
            )
        except RunLimitException as exc:
            if exc.limit_type != scenario.expected_limit:
                raise RuntimeError(f"Scenario '{scenario.name}' expected "
                                   f"{scenario.expected_limit.value}, but received "
                                   f"{exc.limit_type.value}.") from exc
            if exc.configured_value != 1 or exc.observed_value != 2:
                raise RuntimeError("Limit counters were unexpected: "
                                   f"configured={exc.configured_value}, "
                                   f"observed={exc.observed_value}.") from exc
            print(f"\n⛔ [{exc.error_code}: {exc}]")
            print("✅ Invocation 1 raised the expected limit: "
                  f"configured={exc.configured_value}, "
                  f"observed={exc.observed_value}")
        else:
            raise RuntimeError(f"Scenario '{scenario.name}' completed without raising "
                               f"{scenario.expected_limit.value}.")

        print("\n--- Invocation 2/2: continue with the same session ---")
        print("⚙️  Limits disabled for the continuation invocation")
        print(f"📝 User: {_CONTINUATION_QUERY}")
        print("🤖 Assistant: ", end="", flush=True)
        try:
            final_response_received = await _run_invocation(
                runner,
                user_id,
                session_id,
                _CONTINUATION_QUERY,
                continuation_run_config,
            )
        except RunLimitException as exc:
            raise RuntimeError(f"Invocation 2 unexpectedly raised {exc.error_code} even though "
                               "its limits were disabled.") from exc
        if not final_response_received:
            raise RuntimeError("Invocation 2 completed without a final response.")
        print("\n✅ Invocation 2 continued and completed normally")
        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_weather_agent())
