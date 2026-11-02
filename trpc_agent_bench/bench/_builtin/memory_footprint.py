"""Retained memory per resident agent (no tools and the five-tool profile)."""

from __future__ import annotations

import asyncio
import gc
import os
import sys
import tracemalloc

from .. import teardown
from .. import setup
from .._eval import percentile
from .._eval import write_result
from .. import TextBlock
from .. import BenchProfile
from ._tool_fixtures import make_tools

DESCRIPTION = ("批量构造 agent 并一直持有，不释放。"
               "memory = 每个常驻 agent 占用的内存，不是构造过程中的瞬时峰值。")
AGENTS_PER_SAMPLE = max(1, int(os.environ.get("BENCH_MEMORY_AGENTS", "1000")))
SAMPLES = 5
TURN_SCRIPT = [[TextBlock()]]


async def _per_agent_footprint(factory) -> float:
    gc.collect()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    agents = [await factory() for _ in range(AGENTS_PER_SAMPLE)]
    after, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del agents
    gc.collect()
    return max(0.0, (after - before) / (1024.0 * 1024.0)) / AGENTS_PER_SAMPLE


async def _write_memory_result(
    *,
    name: str,
    description: str,
    factory,
    samples: int,
) -> None:
    usages = [await _per_agent_footprint(factory) for _ in range(samples)]
    median = percentile(usages, 50)
    result = {
        "run_times": [],
        "memory_usages": usages,
        "median_run_time": 0.0,
        "p95_run_time": 0.0,
        "avg_run_time": 0.0,
        "min_run_time": 0.0,
        "max_run_time": 0.0,
        "std_run_time": 0.0,
        "median_memory_usage": median,
        "avg_memory_usage": sum(usages) / len(usages),
        "min_memory_usage": min(usages),
        "max_memory_usage": max(usages),
        "num_iterations": samples,
        "warmup_runs": 0,
    }
    write_result(
        name=name,
        result=result,
        description=description,
    )


async def _run() -> None:
    box = await setup(TURN_SCRIPT)
    samples = SAMPLES
    try:
        await _write_memory_result(
            name="memory_per_agent",
            description=(f"批量构造 {AGENTS_PER_SAMPLE} 个不带工具的 agent 并一直持有，不释放。"
                         "指标：memory = 每个常驻 agent 占用的内存，不是构造过程中的瞬时峰值。"),
            factory=lambda: box.target.instantiate(
                box.prepared,
                tools_factory=lambda: [],
            ),
            samples=samples,
        )
        await _write_memory_result(
            name="memory_per_agent_with_tools",
            description=(f"批量构造 {AGENTS_PER_SAMPLE} 个带 5 个工具的 agent 并一直持有，不释放。"
                         "指标：memory = 每个常驻 agent 占用的内存，不是构造过程中的瞬时峰值。"),
            factory=lambda: box.target.instantiate(
                box.prepared,
                tools_factory=make_tools,
            ),
            samples=samples,
        )
    finally:
        await teardown(box)


def get_bench_profile() -> BenchProfile:
    return BenchProfile(requires_iterations=False)


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
