"""Benchmark capability metadata used by ``run_bench`` before execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchProfile:
    """What a benchmark can do and what it needs from the runner."""

    usable: bool = True
    supports_concurrency: bool = False
    requires_iterations: bool = True
    skip_reason: str = ""
