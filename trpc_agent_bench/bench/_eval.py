"""Measurement, statistics, correctness assertions, and result writing."""

from __future__ import annotations

import asyncio
import gc
import inspect
import json
import os
import statistics
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import AsyncIterator
from typing import Callable
from typing import Iterable


def get_iterations() -> int:
    """Return the iteration count selected by ``run_bench --iterations``."""

    raw = os.environ.get("BENCH_ITERATIONS")
    if raw is None:
        raise RuntimeError("BENCH_ITERATIONS is not set; run through run_bench or set it explicitly", )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"BENCH_ITERATIONS must be an integer: {raw!r}") from exc
    if value <= 0:
        raise ValueError("BENCH_ITERATIONS must be positive")
    return value


def percentile(values: list[float], percent: float) -> float:
    """Inclusive linear-interpolation percentile; safe for small samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass
class PerformanceResult:
    name: str
    run_times: list[float]
    memory_usages: list[float]

    @property
    def median_run_time(self) -> float:
        return statistics.median(self.run_times) if self.run_times else 0.0

    @property
    def p95_run_time(self) -> float:
        return percentile(self.run_times, 95)

    @property
    def median_memory_usage(self) -> float:
        return statistics.median(self.memory_usages) if self.memory_usages else 0.0

    def to_dict(self) -> dict[str, Any]:
        run_times = self.run_times
        memory = self.memory_usages
        return {
            "name": self.name,
            "run_times": run_times,
            "memory_usages": memory,
            "median_run_time": self.median_run_time,
            "p95_run_time": self.p95_run_time,
            "avg_run_time": statistics.fmean(run_times) if run_times else 0.0,
            "min_run_time": min(run_times) if run_times else 0.0,
            "max_run_time": max(run_times) if run_times else 0.0,
            "std_run_time": statistics.pstdev(run_times) if len(run_times) > 1 else 0.0,
            "median_memory_usage": self.median_memory_usage,
        }


class PerformanceEval:
    """Warmup, timed pass, then a separate memory pass.

    ``tracemalloc`` slows execution, so the timed pass never starts it. The
    function may be sync or async; async functions run in one event loop for
    the whole eval, not one loop per iteration.
    """

    def __init__(
        self,
        *,
        name: str,
        func: Callable[..., Any],
        num_iterations: int,
        warmup_runs: int = 5,
        measure_memory: bool = True,
        description: str = "",
        display_name: str = "",
        label: str = "",
    ) -> None:
        self.name = name
        self.func = func
        self.num_iterations = max(1, num_iterations)
        self.warmup_runs = max(0, warmup_runs)
        self.measure_memory = measure_memory
        self.description = description
        self.display_name = display_name or name
        self.label = label

    async def run(self) -> PerformanceResult:
        if inspect.iscoroutinefunction(self.func):
            return await self._run_async()
        return self._run_sync()

    def _run_sync(self) -> PerformanceResult:
        for _ in range(self.warmup_runs):
            self.func()
        run_times = [self._timed_sync() for _ in range(self.num_iterations)]
        if not self.measure_memory:
            return PerformanceResult(self.name, run_times, [])
        baseline = self._memory_baseline_sync()
        memory = [self._memory_sync(baseline) for _ in range(self.num_iterations)]
        return PerformanceResult(self.name, run_times, memory)

    async def _run_async(self) -> PerformanceResult:
        for _ in range(self.warmup_runs):
            await self.func()
        run_times = [await self._timed_async() for _ in range(self.num_iterations)]
        if not self.measure_memory:
            return PerformanceResult(self.name, run_times, [])
        baseline = await self._memory_baseline_async()
        memory = [await self._memory_async(baseline) for _ in range(self.num_iterations)]
        return PerformanceResult(self.name, run_times, memory)

    def _timed_sync(self) -> float:
        started = time.perf_counter()
        self.func()
        return time.perf_counter() - started

    async def _timed_async(self) -> float:
        started = time.perf_counter()
        await self.func()
        return time.perf_counter() - started

    def _memory_baseline_sync(self) -> float:
        samples = []
        for _ in range(3):
            gc.collect()
            tracemalloc.start()
            (lambda: None)()
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            samples.append(peak)
        return statistics.fmean(samples)

    async def _memory_baseline_async(self) -> float:
        samples = []
        for _ in range(3):
            gc.collect()
            tracemalloc.start()
            await asyncio.sleep(0)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            samples.append(peak)
        return statistics.fmean(samples)

    def _memory_sync(self, baseline: float) -> float:
        gc.collect()
        tracemalloc.start()
        self.func()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return max(0.0, peak - baseline) / (1024.0 * 1024.0)

    async def _memory_async(self, baseline: float) -> float:
        gc.collect()
        tracemalloc.start()
        await self.func()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return max(0.0, peak - baseline) / (1024.0 * 1024.0)


async def drain(iterator: AsyncIterator[Any]) -> list[Any]:
    """Consume an async event stream into a list."""
    return [item async for item in iterator]


def ensure_turn_completed(
    events: Iterable[Any],
    *,
    expected_text: str | None = None,
    expect_tool_success: bool = False,
) -> None:
    """Fail loudly when a benchmark turn took an error or silent path."""
    event_list = list(events)
    if any(event.is_error() for event in event_list):
        raise RuntimeError("turn returned an error event")

    non_partial: list[str] = []
    partial: list[str] = []
    calls = []
    results = []
    has_non_partial_content = False
    for event in event_list:
        content = getattr(event, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "thought", False):
                continue
            text = getattr(part, "text", None)
            if text:
                if getattr(event, "partial", False):
                    partial.append(text)
                else:
                    non_partial.append(text)
                    has_non_partial_content = True
            if getattr(part, "function_call", None) is not None:
                calls.append(part.function_call)
            if getattr(part, "function_response", None) is not None:
                results.append(part.function_response)
                has_non_partial_content = True

    turn_complete = any(
        getattr(event, "turn_complete", False) or (
            callable(getattr(event, "is_final_response", None)) and event.is_final_response()) for event in event_list)
    if not turn_complete and not has_non_partial_content and not partial:
        raise RuntimeError("turn did not complete")

    final_text = "".join(non_partial) or "".join(partial)
    if expected_text is not None and final_text.strip() != expected_text.strip():
        raise RuntimeError(f"unexpected final text: {final_text!r}")

    if expect_tool_success:
        if not calls or not results:
            raise RuntimeError("tool loop did not execute")
        for response in results:
            payload = getattr(response, "response", None)
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError("tool call failed")


def _payload(
    *,
    name: str,
    result: dict[str, Any],
    description: str = "",
    display_name: str = "",
    label: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "display_name": display_name or name,
        "label": label,
        "description": description,
        "target": os.environ.get("BENCH_TARGET", ""),
        "num_iterations": result.get("num_iterations"),
        "warmup_runs": result.get("warmup_runs"),
        "result": result,
    }


def write_result(
    *,
    name: str,
    result: dict[str, Any],
    description: str = "",
    results_dir: str | Path | None = None,
    display_name: str = "",
    label: str = "",
) -> dict[str, Any]:
    description = description or os.environ.get("BENCH_DESCRIPTION", "")
    payload = _payload(
        name=name,
        result=result,
        description=description,
        display_name=display_name,
        label=label,
    )
    raw_directory = results_dir or os.environ.get("BENCH_RESULTS_DIR", "")
    if raw_directory:
        directory = Path(raw_directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    summary = {
        "name": name,
        "target": payload["target"],
        "median_run_time": result.get("median_run_time"),
        "p95_run_time": result.get("p95_run_time"),
    }
    if not os.environ.get("BENCH_QUIET"):
        print(json.dumps(summary, ensure_ascii=False))
    return payload


async def run_benchmarks(
    evals: list[PerformanceEval],
    *,
    results_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Run evals inside the caller's event loop."""
    payloads = []
    for evaluation in evals:
        result = (await evaluation.run()).to_dict()
        result["name"] = evaluation.name
        result["num_iterations"] = evaluation.num_iterations
        result["warmup_runs"] = evaluation.warmup_runs
        payloads.append(
            write_result(
                name=evaluation.name,
                result=result,
                description=evaluation.description,
                results_dir=results_dir,
                display_name=evaluation.display_name,
                label=evaluation.label,
            ), )
    return payloads
