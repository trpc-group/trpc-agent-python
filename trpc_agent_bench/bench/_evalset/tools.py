"""One round of agent execution: one model-requested ``add_numbers`` call, then a final answer."""

from __future__ import annotations

import asyncio
import sys
from typing import List

from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import FunctionTool

from ...core import teardown
from .._utils._common import new_session_id
from ...core import setup
from ...core import PerformanceEval
from ...core import drain
from ...core import ensure_turn_completed
from ...core import get_iterations
from ...core import run_benchmarks
from ...core import FunctionBlock
from ...core import TextBlock

DESCRIPTION = ("一轮 Agent 执行内，模型先请求 add_numbers tool call，执行后再返回最终文本。"
               "指标：p50/p95 = 整轮 Agent 执行的耗时；memory = 本次采样中 tracemalloc 记录的峰值。")
TURN_SCRIPT = [
    [
        [FunctionBlock("add_numbers", {
            "a": 1,
            "b": 2
        })],
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
                message="Add 1 and 2.",
            ), )
        ensure_turn_completed(
            events,
            expected_text="ok",
            expect_tool_success=True,
        )

    try:
        await run_benchmarks([
            PerformanceEval(
                name="tools",
                func=arun_agent,
                num_iterations=get_iterations(),
                warmup_runs=10,
            ),
        ], )
    finally:
        await teardown(box)


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
