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
from pathlib import Path

import pytest

from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisCondition

from .replay_cases import REPLAY_CASES
from .replay_harness import INJECTED_FAULTS
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
        await create_sqlite_backend(name="sql_fallback"),
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
    assert "temp:retry_buffer" not in partial_snapshot["session"]["state"]

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


async def test_redis_mock_uses_storage_query_and_ttl_paths():
    """The network-free Redis backend still exercises RedisStorage behavior."""

    backend = await create_mock_redis_backend(enable_ttl=True)
    memory_case = next(case for case in REPLAY_CASES if case.case_id == "memory_roundtrip")
    session_storage = backend.session_service._redis_storage  # pylint: disable=protected-access
    memory_storage = backend.memory_service._redis_storage  # pylint: disable=protected-access
    try:
        snapshot = await execute_case(backend, memory_case)
        assert snapshot["memory"]["preference-token-oolong"]
        assert {"type", "lrange", "expire"}.issubset(memory_storage.command_names)
        assert memory_storage._server.expirations  # pylint: disable=protected-access
    finally:
        await backend.close()
    assert session_storage.closed
    assert memory_storage.closed


async def test_redis_mock_supports_storage_query_types():
    """RedisStorage query dispatch remains valid for every supported data type."""

    backend = await create_mock_redis_backend()
    storage = backend.memory_service._redis_storage  # pylint: disable=protected-access
    try:
        async with storage.create_db_session() as redis_session:
            commands = (
                RedisCommand(method="set", args=("probe:string", b'"value"')),
                RedisCommand(method="hset", args=("probe:hash", "field", "value")),
                RedisCommand(method="rpush", args=("probe:list", "first", "second")),
                RedisCommand(method="sadd", args=("probe:set", "member")),
                RedisCommand(method="zadd", args=("probe:zset", {
                    "member": 1.0
                })),
            )
            for command in commands:
                await storage.execute_command(redis_session, command)
            result = dict(await storage.query(redis_session, "probe:*", RedisCondition()))

        assert result["probe:string"] == "value"
        assert result["probe:hash"] == {"field": "value"}
        assert result["probe:list"] == ["first", "second"]
        assert result["probe:set"] == {"member"}
        assert result["probe:zset"] == [("member", 1.0)]
        assert {"type", "get", "hgetall", "lrange", "smembers", "zrange"}.issubset(storage.command_names)
    finally:
        await backend.close()


async def test_replay_matrix_rejects_empty_inputs():
    """Reusable matrix validation must fail clearly instead of dividing by zero."""

    invalid_inputs = (
        ({
            "cases": ()
        }, "At least one replay case"),
        ({
            "injected_faults": ()
        }, "At least one injected fault"),
        ({
            "injected_faults": (INJECTED_FAULTS[0], )
        }, "At least one summary fault"),
    )
    for kwargs, message in invalid_inputs:
        backend = await create_in_memory_backend()
        try:
            with pytest.raises(ValueError, match=message):
                await run_replay_matrix([backend], **kwargs)
        finally:
            await backend.close()


async def test_sqlite_fallbacks_use_isolated_database_files():
    """Each fallback owns a database file and removes it on close."""

    first = await create_sqlite_backend(name="first_sqlite")
    second = await create_sqlite_backend(name="second_sqlite")
    first_path = Path(first.session_service._sql_storage._db_engine.url.database)  # pylint: disable=protected-access
    second_path = Path(second.session_service._sql_storage._db_engine.url.database)  # pylint: disable=protected-access
    try:
        assert first_path != second_path
        assert first_path.is_file()
        assert second_path.is_file()
    finally:
        await second.close()
        await first.close()
    assert not first_path.exists()
    assert not second_path.exists()


async def test_sql_integration_or_sqlite_fallback():
    """Use configured SQL or exercise the generic SQL factory with SQLite."""

    sql_url = os.getenv("TRPC_REPLAY_SQL_URL")
    sql_backend = (await create_sql_backend(sql_url, name="sql") if sql_url else await create_sqlite_backend(
        name="sql_fallback"))
    report = await run_replay_matrix([await create_in_memory_backend(), sql_backend])
    assert all(result["passed"] for result in report["normal_cases"])
    assert report["metrics"]["injected_detection_rate"] == 1.0
    assert report["backends"][1] == ("sql" if sql_url else "sql_fallback")
