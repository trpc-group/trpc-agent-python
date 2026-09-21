# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run a real-model large-object comparison for Tool Calling and CodeAct."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import get_model_config
from dotenv import load_dotenv
from pydantic import BaseModel

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.codeact import CodeActConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content, Part

load_dotenv(Path(__file__).with_name(".env"))

COUNT = int(os.getenv("CODEACT_COMPARISON_COUNT", "40000"))
DIVISOR = 7
EXPECTED_VALUE = sum(
    number * number
    for number in range(COUNT)
    if number % DIVISOR == 0
)
QUERY = (
    f"必须调用 load_numbers(count={COUNT}) 恰好一次。计算返回数据中所有能被 "
    f"{DIVISOR} 整除数字的平方和，并返回精确的数据范围、表达式和数值。"
)


class CalculationResult(BaseModel):
    """Structured result required from both real-model paths."""

    expression: str
    value: int


@dataclass
class RunMetrics:
    """Observed metrics from one real Agent invocation."""

    mode: str
    llm_calls: int = 0
    tool_calls: int = 0
    request_chars: list[int] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: float = 0
    result: str = ""
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def second_request_chars(self) -> int | None:
        return self.request_chars[1] if len(self.request_chars) > 1 else None


def create_model() -> LLMModel:
    """Create the configured real OpenAI-compatible model."""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
    )


def create_load_numbers(metrics: RunMetrics) -> FunctionTool:
    """Create the same large-object tool for both paths."""

    def load_numbers(count: int) -> list[int]:
        """Return integers from zero up to, but excluding, count."""
        metrics.tool_calls += 1
        return list(range(count))

    return FunctionTool(load_numbers)


def create_callbacks(metrics: RunMetrics):
    """Record actual requests and provider-reported usage."""

    async def before_model(_context, request: LlmRequest):
        metrics.llm_calls += 1
        request_data = request.model_dump(mode="json", exclude_none=True)
        serialized = json.dumps(
            request_data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        metrics.request_chars.append(len(serialized))

    async def after_model(_context, response: LlmResponse):
        if response.error_code:
            metrics.error = (
                f"{response.error_code}: "
                f"{response.error_message or 'model call failed'}"
            )
        usage = response.usage_metadata
        if usage is None:
            return
        metrics.prompt_tokens += usage.prompt_token_count or 0
        metrics.completion_tokens += usage.candidates_token_count or 0

    return before_model, after_model


def create_agent(mode: str, metrics: RunMetrics) -> LlmAgent:
    """Create a real-model Agent using the same low-level tool."""
    before_model, after_model = create_callbacks(metrics)
    common: dict[str, Any] = {
        "name": f"{mode.lower().replace(' ', '_')}_large_object_agent",
        "model": create_model(),
        "tools": [create_load_numbers(metrics)],
        "output_schema": CalculationResult,
        "before_model_callback": before_model,
        "after_model_callback": after_model,
    }
    if mode == "Tool Calling":
        common["instruction"] = (
            "You must use ordinary function calling. Call load_numbers exactly "
            "once with the requested count. Inspect the returned complete list, "
            "then return the requested structured result. Do not skip the tool."
        )
    else:
        common.update(
            instruction=(
                "Complete the task in exactly two CodeAct cells. First call "
                "await self.load_numbers(count=...), save the result in global "
                "variable nums, print len(nums) and doc(nums), and do not call "
                "return_result. In the second cell reuse nums, calculate and "
                "verify the answer, then call return_result. Never load again."
            ),
            code_act=CodeActConfig(),
        )
    return LlmAgent(**common)


async def run_once(mode: str) -> RunMetrics:
    """Run one real invocation and preserve failures as benchmark results."""
    metrics = RunMetrics(mode=mode)
    app_name = f"codeact_real_comparison_{mode.lower().replace(' ', '_')}"
    user_id = "comparison_user"
    session_id = str(uuid.uuid4())
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    runner = Runner(
        app_name=app_name,
        agent=create_agent(mode, metrics),
        session_service=session_service,
    )

    events: list[Event] = []
    started = time.perf_counter()
    try:
        events = [
            event
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=Content(role="user", parts=[Part(text=QUERY)]),
            )
        ]
        metrics.result = extract_result(events)
        if metrics.result == "(no final result)" and not metrics.error:
            metrics.error = "Model returned no final result"
        elif not metrics.error:
            parsed = json.loads(metrics.result)
            if parsed.get("value") != EXPECTED_VALUE:
                metrics.error = (
                    f"Incorrect result: expected {EXPECTED_VALUE}, "
                    f"got {parsed.get('value')}"
                )
    except Exception as exc:  # noqa: BLE001
        metrics.error = f"{type(exc).__name__}: {exc}"
    finally:
        metrics.elapsed_ms = (time.perf_counter() - started) * 1000
    return metrics


def extract_result(events: list[Event]) -> str:
    """Extract the final structured result from either path."""
    codeact_results = [
        event.get_text() for event in events if event.object == "codeact.result"
    ]
    if codeact_results:
        return codeact_results[-1]
    final_texts = [
        event.get_text()
        for event in events
        if event.is_final_response() and event.get_text()
    ]
    return final_texts[-1] if final_texts else "(no final result)"


def format_optional(value: int | None) -> str:
    """Format a metric that may be absent after an early failure."""
    return str(value) if value is not None else "N/A"


def print_results(results: list[RunMetrics]) -> None:
    """Render measurements from the real provider calls."""
    print(f"Real-model large-object benchmark: load_numbers(count={COUNT})")
    print("Both paths use the same model, tool, query, and output schema.\n")
    print(
        "Mode         | LLM | Tool | 2nd request chars | Prompt | "
        "Completion | Total | Elapsed ms | Status"
    )
    print(
        "-------------+-----+------+-------------------+--------+"
        "------------+-------+------------+--------"
    )
    for result in results:
        status = "FAILED" if result.error else "OK"
        print(
            f"{result.mode:<12} | {result.llm_calls:>3} | "
            f"{result.tool_calls:>4} | "
            f"{format_optional(result.second_request_chars):>17} | "
            f"{result.prompt_tokens:>6} | {result.completion_tokens:>10} | "
            f"{result.total_tokens:>5} | {result.elapsed_ms:>10.1f} | {status}"
        )

    print("\nResults:")
    for result in results:
        detail = result.error or result.result
        print(f"- {result.mode}: {detail}")

    traditional, codeact = results
    if (
        traditional.second_request_chars is not None
        and codeact.second_request_chars is not None
    ):
        reduction = (
            1
            - codeact.second_request_chars / traditional.second_request_chars
        )
        print(
            f"\nCodeAct reduced the actual second request size by "
            f"{reduction:.2%}."
        )


async def main() -> None:
    """Run both mechanisms against the configured real model."""
    results = [
        await run_once("Tool Calling"),
        await run_once("CodeAct"),
    ]
    print_results(results)


if __name__ == "__main__":
    asyncio.run(main())
