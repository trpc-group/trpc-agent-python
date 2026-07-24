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

from .replay_cases import REPLAY_CASES
from .replay_harness import create_in_memory_backend
from .replay_harness import create_mock_redis_backend
from .replay_harness import create_redis_backend
from .replay_harness import create_sql_backend
from .replay_harness import create_sqlite_backend
from .replay_harness import execute_case
from .replay_harness import run_replay_matrix
from .replay_harness import write_report


async def test_replay_matrix_meets_acceptance(tmp_path):
    """All dependency-free backends must agree and detect every corruption."""

    report = await run_replay_matrix([
        await create_in_memory_backend(),
        await create_sqlite_backend(),
        await create_sql_backend("sqlite:///:memory:", name="sql_fallback"),
        await create_mock_redis_backend(),
    ])
    report_path = tmp_path / "session_memory_summary_diff_report.json"
    write_report(report, report_path)
    persisted_report = json.loads(report_path.read_text(encoding="utf-8"))

    assert persisted_report["case_count"] == 10
    assert persisted_report["backends"] == ["in_memory", "sqlite", "sql_fallback", "redis_mock"]
    assert len(persisted_report["normal_cases"]) == 10
    assert len(persisted_report["injected_cases"]) == 10
    assert persisted_report["metrics"]["false_positive_rate"] <= 0.05
    assert persisted_report["metrics"]["injected_detection_rate"] == 1.0
    assert persisted_report["metrics"]["summary_fault_detection_rate"] == 1.0
    assert persisted_report["metrics"]["duration_seconds"] <= 30
    assert all(result["passed"] for result in persisted_report["normal_cases"])

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
    started_cases = []
    snapshots = {}
    started_at = time.perf_counter()
    try:
        for case in REPLAY_CASES:
            snapshot = await execute_case(backend, case)
            assert snapshot["session"]["id"] == f"session-{case.case_id}"
            assert all(snapshot["checks"].values())
            snapshots[case.case_id] = snapshot
            started_cases.append(case.case_id)
    finally:
        await backend.close()

    final_state = snapshots["state_overwrite"]["session"]["state"]
    assert final_state["phase"] == "done"
    assert final_state["counter"] == 3
    assert snapshots["memory_roundtrip"]["memory"]["preference-token-oolong"]

    update_snapshot = snapshots["summary_update"]
    assert [revision["version"] for revision in update_snapshot["summary_history"]] == [1, 2]
    assert all(revision["updated_after_previous"] for revision in update_snapshot["summary_history"])
    assert update_snapshot["summary"]["summary_text"] == "summary update version two"

    truncation_snapshot = snapshots["summary_truncation"]
    assert truncation_snapshot["summary"]["session_id"] == "session-summary_truncation"
    assert truncation_snapshot["session"]["events"][0]["is_summary"]
    assert truncation_snapshot["session"]["historical_events"]
    assert [event["content"]["parts"][0]["text"] for event in truncation_snapshot["session"]["events"][-2:]
            ] == ["follow-up after compression", "answer after compression"]

    partial_snapshot = snapshots["partial_retry"]
    final_events = partial_snapshot["session"]["events"]
    assert [event["id"] for event in final_events].count("retry-event") == 1
    assert final_events[-1]["content"]["parts"][0]["text"] == "finished answer"
    assert partial_snapshot["session"]["state"]["recovery_status"] == "complete"

    assert len(started_cases) == 10
    assert time.perf_counter() - started_at <= 30


async def test_redis_integration_or_mock_fallback():
    """Use configured Redis or exercise Redis services with in-process storage."""

    redis_url = os.getenv("TRPC_REPLAY_REDIS_URL")
    redis_backend = await create_redis_backend(redis_url) if redis_url else await create_mock_redis_backend()
    report = await run_replay_matrix([await create_in_memory_backend(), redis_backend])
    assert all(result["passed"] for result in report["normal_cases"])
    assert report["metrics"]["injected_detection_rate"] == 1.0
    assert report["backends"][1] == ("redis" if redis_url else "redis_mock")


async def test_sql_integration_or_sqlite_fallback():
    """Use configured SQL or exercise the generic SQL factory with SQLite."""

    sql_url = os.getenv("TRPC_REPLAY_SQL_URL")
    sql_backend = (await create_sql_backend(sql_url, name="sql")
                   if sql_url else await create_sql_backend("sqlite:///:memory:", name="sql_fallback"))
    report = await run_replay_matrix([await create_in_memory_backend(), sql_backend])
    assert all(result["passed"] for result in report["normal_cases"])
    assert report["metrics"]["injected_detection_rate"] == 1.0
    assert report["backends"][1] == ("sql" if sql_url else "sql_fallback")
