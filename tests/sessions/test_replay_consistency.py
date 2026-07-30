"""Cross-backend replay consistency and fault-detection acceptance tests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService, SessionServiceConfig, SqlSessionService

from .replay_harness import MUTATIONS, ReplayBackend, compare_snapshots, load_cases, mutate, replay_case


ROOT = Path(__file__).parents[2]
CASES = load_cases(ROOT / "replay_cases/session_memory_summary.json")


async def _backends() -> list[ReplayBackend]:
    config = SessionServiceConfig(store_historical_events=True)
    memory = ReplayBackend("in_memory", InMemorySessionService(session_config=config.model_copy(deep=True)))
    sqlite_service = SqlSessionService(db_url="sqlite:///:memory:", is_async=False,
                                       session_config=config.model_copy(deep=True))
    sqlite_memory = SqlMemoryService(db_url="sqlite:///:memory:", is_async=False, enabled=True)
    await sqlite_service._sql_storage.create_sql_engine()
    await sqlite_memory._sql_storage.create_sql_engine()
    return [memory, ReplayBackend("sqlite", sqlite_service, memory_service=sqlite_memory)]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_replay_case_is_consistent(case):
    backends = await _backends()
    try:
        snapshots = [await replay_case(backend, case) for backend in backends]
        diffs = compare_snapshots(snapshots[0], snapshots[1])
        assert not diffs, json.dumps(diffs, ensure_ascii=False, indent=2)
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("mutation", [name for name, _ in MUTATIONS])
async def test_detects_injected_inconsistency(mutation):
    # Each mutation uses a snapshot containing the relevant event/state/memory/summary fields.
    case = {
        "name": f"mutation-{mutation}",
        "operations": [
            {"op": "event", "id": "e1", "author": "user", "text": "jasmine preference"},
            {"op": "event", "id": "e2", "author": "agent", "text": "noted"},
            {"op": "state", "id": "e3", "values": {"theme": "dark"}},
            {"op": "memory", "query": "jasmine"},
            {"op": "summary", "id": "sum-1", "version": 1, "text": "Preference recorded."},
        ],
    }
    backend = ReplayBackend("in_memory", InMemorySessionService())
    try:
        snapshot = await replay_case(backend, case)
        diffs = compare_snapshots(snapshot, mutate(snapshot, mutation))
        assert diffs, f"mutation {mutation} was not detected"
        assert all({"path", "left", "right"} <= diff.keys() for diff in diffs)
    finally:
        await backend.close()


async def test_lightweight_suite_budget_and_report(tmp_path):
    started = time.monotonic()
    backends = await _backends()
    report = {"mode": "lightweight", "backends": [b.name for b in backends], "cases": []}
    try:
        for case in CASES:
            snapshots = [await replay_case(backend, case) for backend in backends]
            report["cases"].append({"case": case["name"], "session_id": snapshots[0]["session_id"],
                                    "differences": compare_snapshots(*snapshots)})
    finally:
        for backend in backends:
            await backend.close()
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    assert report["duration_seconds"] < 30
    assert all(not case["differences"] for case in report["cases"])
    output = tmp_path / "session_memory_summary_diff_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["cases"]


@pytest.mark.skipif(not os.getenv("TRPC_REPLAY_SQL_DB_URL"),
                    reason="set TRPC_REPLAY_SQL_DB_URL to enable external SQL replay")
async def test_external_sql_integration():
    db_url = os.environ["TRPC_REPLAY_SQL_DB_URL"]
    session_service = SqlSessionService(db_url=db_url, is_async=False)
    memory_service = SqlMemoryService(db_url=db_url, is_async=False, enabled=True)
    await session_service._sql_storage.create_sql_engine()
    await memory_service._sql_storage.create_sql_engine()
    persistent = ReplayBackend("external_sql", session_service, memory_service=memory_service)
    reference = ReplayBackend("in_memory", InMemorySessionService())
    try:
        for case in CASES:
            assert not compare_snapshots(await replay_case(reference, case), await replay_case(persistent, case))
    finally:
        await reference.close()
        await persistent.close()
