"""Agent construction only; the model mock and runtime are already prepared."""

from __future__ import annotations

import asyncio
import sys

from .. import teardown
from .. import setup
from .. import PerformanceEval
from .. import get_iterations
from .. import run_benchmarks
from .. import TextBlock

DESCRIPTION = ("只调用 target.instantiate() 构造一个不带工具的 agent，不执行 turn。"
               "指标：p50/p95 = 单次构造耗时；memory = 构造过程中的 tracemalloc 峰值。")
TURN_SCRIPT = [[TextBlock()]]


async def _run() -> None:
    box = await setup(TURN_SCRIPT)

    async def make_agent():
        agent = await box.target.instantiate(box.prepared, tools_factory=lambda: [])
        if agent is None:
            raise RuntimeError("target.instantiate returned None")
        return agent

    try:
        await run_benchmarks([
            PerformanceEval(
                name="instantiate_agent",
                func=make_agent,
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
