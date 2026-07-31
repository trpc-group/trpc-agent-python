# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic 500-line hot-path performance acceptance."""

from __future__ import annotations

import statistics
import time

from trpc_agent_sdk.safety import SafetyDecision
from trpc_agent_sdk.safety import SafetyScanRequest


def _measure(scanner, source: str, language: str, runs: int = 12) -> tuple[float, float, float]:
    request = SafetyScanRequest(script=source, language=language, source_type="performance")
    for _ in range(3):
        scanner.scan(request)
    durations = []
    for _ in range(runs):
        started = time.perf_counter_ns()
        report = scanner.scan(request)
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
        assert report.decision is SafetyDecision.ALLOW
    ordered = sorted(durations)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return statistics.median(ordered), p95, max(ordered)


def test_python_500_lines_is_below_one_second(scanner):
    source = "\n".join(f"value_{index} = {index}" for index in range(500))
    median, p95, maximum = _measure(scanner, source, "python")
    assert median < 1_000
    assert p95 < 1_000
    assert maximum < 1_000


def test_shell_500_lines_is_below_one_second(scanner):
    source = "\n".join(f"echo line-{index}" for index in range(500))
    median, p95, maximum = _measure(scanner, source, "shell")
    assert median < 1_000
    assert p95 < 1_000
    assert maximum < 1_000
