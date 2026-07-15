# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Acceptance tests for Session/Memory/Summary replay consistency."""

from __future__ import annotations

import json
import os
import time

import pytest

from .replay_cases import REPLAY_CASES
from .replay_harness import create_in_memory_backend
from .replay_harness import create_redis_backend
from .replay_harness import create_sqlite_backend
from .replay_harness import execute_case
from .replay_harness import run_replay_matrix
from .replay_harness import write_report


async def test_replay_matrix_meets_acceptance(tmp_path):
    """SQLite and InMemory must agree and detect all ten public corruptions."""

    report = await run_replay_matrix([await create_in_memory_backend(), await create_sqlite_backend()])
    report_path = tmp_path / "session_memory_summary_diff_report.json"
    write_report(report, report_path)
    persisted_report = json.loads(report_path.read_text(encoding="utf-8"))

    assert persisted_report["case_count"] == 10
    assert len(persisted_report["normal_cases"]) == 10
    assert len(persisted_report["injected_cases"]) == 10
    assert persisted_report["metrics"]["false_positive_rate"] <= 0.05
    assert persisted_report["metrics"]["injected_detection_rate"] == 1.0
    assert persisted_report["metrics"]["summary_fault_detection_rate"] == 1.0
    assert persisted_report["metrics"]["duration_seconds"] <= 30

    all_injected_diffs = [
        difference for result in persisted_report["injected_cases"] for difference in result["differences"]
        if not difference["allowed"]
    ]
    assert all(difference["session_id"] and difference["path"].startswith("$") for difference in all_injected_diffs)
    assert all(
        any(difference["event_index"] is not None for difference in result["differences"])
        for result in persisted_report["injected_cases"]
        if result["fault_id"] in {"missing_event", "event_order", "tool_response", "duplicate_retry"})
    assert all(
        any(difference["summary_id"] for difference in result["differences"])
        for result in persisted_report["injected_cases"] if result["fault_id"].startswith("summary_"))


async def test_in_memory_lightweight_mode():
    """All traces can run without SQL, Redis, Docker, or a network model."""

    backend = await create_in_memory_backend()
    retry_backend = None
    started_cases = []
    started_at = time.perf_counter()
    try:
        for case in REPLAY_CASES:
            snapshot = await execute_case(backend, case)
            assert snapshot["session"]["id"] == f"session-{case.case_id}"
            started_cases.append(case.case_id)

        retry_backend = await create_in_memory_backend()
        partial_snapshot = await execute_case(retry_backend, REPLAY_CASES[-1])
        final_events = partial_snapshot["session"]["events"]
        assert [event["id"] for event in final_events].count("retry-event") == 1
        assert final_events[-1]["content"]["parts"][0]["text"] == "finished answer"
    finally:
        if retry_backend is not None:
            await retry_backend.close()
        await backend.close()

    assert len(started_cases) == 10
    assert time.perf_counter() - started_at <= 30


@pytest.mark.skipif(not os.getenv("TRPC_REPLAY_REDIS_URL"), reason="TRPC_REPLAY_REDIS_URL is not configured")
async def test_redis_integration_when_configured():
    """A real Redis backend joins the same matrix only when explicitly configured."""

    redis_url = os.environ["TRPC_REPLAY_REDIS_URL"]
    report = await run_replay_matrix([await create_in_memory_backend(), await create_redis_backend(redis_url)])
    assert all(result["passed"] for result in report["normal_cases"])
    assert report["metrics"]["injected_detection_rate"] == 1.0
