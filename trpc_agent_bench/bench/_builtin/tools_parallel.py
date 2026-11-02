"""Parallel tool calls: one assistant response requests three tools."""

from __future__ import annotations

import asyncio
import sys
from typing import List

from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import FunctionTool

from .. import teardown
from ._utils._common import new_session_id
from .. import setup
from .. import PerformanceEval
from .. import drain
from .. import ensure_turn_completed
from .. import get_iterations
from .. import run_benchmarks
from .. import FunctionBlock
from .. import TextBlock

DESCRIPTION = ("一次回复里返回 3 个不同参数的 tool call。"
               "指标：p50/p95 = 整次 turn 的耗时；memory = 本次采样中 tracemalloc 记录的峰值。")
TURN_SCRIPT = [
    [
        [
            FunctionBlock("add_numbers", {
                "a": 1,
                "b": 2
            }),
            FunctionBlock("add_numbers", {
                "a": 2,
                "b": 3
            }),
            FunctionBlock("add_numbers", {
                "a": 3,
                "b": 4
            }),
        ],
        TextBlock(),
    ],
]


def add_numbers(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


def make_tools() -> List[BaseTool]:
    return [FunctionTool(add_numbers)]


async def _run() -> None:
    box = await setup(TURN_SCRIPT)
    handle = await box.target.handle(box.prepared, tools_factory=make_tools)

    async def arun_agent() -> None:
        events = await drain(
            handle.run_turn(
                app_name=box.conf.app_name,
                user_id=box.conf.user_id,
                session_id=new_session_id(),
                message="Add three pairs.",
            ), )
        ensure_turn_completed(
            events,
            expected_text="ok",
            expect_tool_success=True,
        )
        responses = sum(1 for event in events if event.content is not None for part in event.content.parts
                        if part.function_response is not None)
        if responses < 3:
            raise RuntimeError(f"expected 3 tool responses, got {responses}")

    try:
        await run_benchmarks([
            PerformanceEval(
                name="tools_parallel",
                func=arun_agent,
                num_iterations=get_iterations(),
                warmup_runs=2,
            ),
        ], )
    finally:
        await teardown(box)


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
