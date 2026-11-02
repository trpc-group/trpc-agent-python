"""Import time in a fresh interpreter, minus interpreter startup."""

from __future__ import annotations

import statistics
import subprocess
import sys
import time

from ...core._eval import percentile
from ...core._eval import write_result
from ...core import BenchProfile
from ...core._target import load_target

DESCRIPTION = ("每次新开一个 Python 进程 import 被测 Agent 包，再减去只启动解释器的时间。"
               "指标：p50/p95 = 新进程 import 耗时。")
SAMPLES = 15


def _run(command: list[str]) -> float:
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr}", )
    return elapsed


def get_bench_profile() -> BenchProfile:
    return BenchProfile(requires_iterations=False)


def main() -> int:
    target = load_target()
    samples = SAMPLES
    imports = [_run([sys.executable, "-c", f"import {target.import_module}"]) for _ in range(samples)]
    baselines = [_run([sys.executable, "-c", "pass"]) for _ in range(samples)]
    baseline = statistics.median(baselines)
    adjusted = [max(0.0, sample - baseline) for sample in imports]

    result = {
        "run_times": adjusted,
        "memory_usages": [],
        "median_run_time": percentile(adjusted, 50),
        "p95_run_time": percentile(adjusted, 95),
        "avg_run_time": statistics.fmean(adjusted),
        "min_run_time": min(adjusted),
        "max_run_time": max(adjusted),
        "std_run_time": statistics.pstdev(adjusted) if len(adjusted) > 1 else 0.0,
        "median_memory_usage": 0.0,
        "num_iterations": len(adjusted),
        "warmup_runs": 0,
    }
    write_result(name="import_time", result=result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
