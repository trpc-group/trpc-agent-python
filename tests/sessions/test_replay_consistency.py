# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.

"""Cross-backend replay consistency tests for session, memory and summary."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SqlSessionService

from .replay_harness import ReplayBackend
from .replay_harness import build_diff_report
from .replay_harness import compare_snapshots
from .replay_harness import load_replay_cases
from .replay_harness import replay_case

CASE_FILE = Path(__file__).with_name("replay_cases") / "session_memory_summary.jsonl"


def _session_config() -> SessionServiceConfig:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return config


def _memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _in_memory_backend() -> ReplayBackend:
    return ReplayBackend(
        name="in_memory",
        session_service=InMemorySessionService(session_config=_session_config()),
        memory_service=InMemoryMemoryService(memory_service_config=_memory_config()),
    )


def _sqlite_backend(tmp_path: Path) -> ReplayBackend:
    return ReplayBackend(
        name="sqlite",
        session_service=SqlSessionService(
            db_url=f"sqlite:///{tmp_path / 'sessions.db'}",
            session_config=_session_config(),
        ),
        memory_service=SqlMemoryService(
            db_url=f"sqlite:///{tmp_path / 'memory.db'}",
            memory_service_config=_memory_config(),
        ),
    )


def _redis_backend(redis_url: str) -> ReplayBackend:
    return ReplayBackend(
        name="redis",
        session_service=RedisSessionService(
            db_url=redis_url,
            session_config=_session_config(),
        ),
        memory_service=RedisMemoryService(
            db_url=redis_url,
            memory_service_config=_memory_config(),
        ),
    )


@pytest.fixture(scope="module")
async def replay_result(tmp_path_factory):
    cases = load_replay_cases(CASE_FILE)
    backends = [_in_memory_backend()]
    if os.getenv("TRPC_REPLAY_LIGHTWEIGHT") != "1":
        backends.append(_sqlite_backend(tmp_path_factory.mktemp("replay-sqlite")))
    if redis_url := os.getenv("TRPC_REPLAY_REDIS_URL"):
        backends.append(_redis_backend(redis_url))

    snapshots: dict[str, dict[str, dict[str, Any]]] = {}
    try:
        for backend in backends:
            snapshots[backend.name] = {}
            for case in cases:
                snapshots[backend.name][case.case_id] = await replay_case(backend, case)
        yield cases, snapshots, build_diff_report(snapshots, cases)
    finally:
        for backend in reversed(backends):
            await backend.close()


def test_public_fixture_contains_ten_required_cases(replay_result):
    cases, _, _ = replay_result
    assert len(cases) == 10
    assert {case.case_id for case in cases} == {
        "single_turn",
        "multi_turn",
        "tool_round_trip",
        "state_overwrite",
        "memory_preference",
        "memory_fact_update",
        "summary_create",
        "summary_update",
        "summary_truncation",
        "retry_recovery",
    }


def test_normal_replays_have_no_unexpected_differences(replay_result):
    _, _, report = replay_result
    unexpected = [
        difference for case in report["cases"] for difference in case["differences"] if not difference["allowed"]
    ]
    assert unexpected == []
    assert all(case["status"] == "match" for case in report["cases"])


def test_diff_report_is_machine_readable_and_locatable(replay_result, tmp_path):
    _, _, report = replay_result
    report_path = tmp_path / "session_memory_summary_diff_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    restored = json.loads(report_path.read_text(encoding="utf-8"))
    assert restored["schema_version"] == 1
    assert restored["reference_backend"] == "in_memory"
    assert len(restored["cases"]) == 10
    assert all("session_id" in case and "differences" in case for case in restored["cases"])


def test_allowed_diff_only_exempts_declared_metadata(replay_result):
    cases, snapshots, _ = replay_result
    case = next(item for item in cases if item.case_id == "single_turn")
    reference = snapshots["in_memory"][case.case_id]
    candidate = copy.deepcopy(reference)
    candidate["events"][0]["timestamp"] = "backend-specific"
    candidate["events"][0]["author"] = "wrong-author"

    differences = compare_snapshots(
        reference,
        candidate,
        reference_backend="in_memory",
        candidate_backend="injected_fault",
        allowed_diff=case.allowed_diff,
    )
    by_path = {difference["path"]: difference for difference in differences}
    assert by_path["events[0].timestamp"]["allowed"] is True
    assert by_path["events[0].author"]["allowed"] is False


def _inject_difference(case_id: str, snapshot: dict[str, Any]) -> None:
    if case_id == "single_turn":
        snapshot["events"][0]["author"] = "wrong-author"
    elif case_id == "multi_turn":
        snapshot["events"].pop(2)
    elif case_id == "tool_round_trip":
        snapshot["events"][2]["content"] = {"corrupted": True}
    elif case_id == "state_overwrite":
        snapshot["state"]["theme"] = "light"
    elif case_id == "memory_preference":
        snapshot["memory"]["preference"].clear()
    elif case_id == "memory_fact_update":
        snapshot["memory"]["fact"][0]["author"] = "assistant"
    elif case_id == "summary_create":
        snapshot["summary"]["current"] = None
    elif case_id == "summary_update":
        snapshot["summary"]["current"]["version"] = 1
    elif case_id == "summary_truncation":
        snapshot["summary"]["current"]["session_id"] = "another-session"
    elif case_id == "retry_recovery":
        snapshot["events"].append(copy.deepcopy(snapshot["events"][0]))
    else:
        raise AssertionError(f"No mutation configured for {case_id}")


def test_all_ten_injected_inconsistencies_are_detected(replay_result):
    cases, snapshots, _ = replay_result
    reference_snapshots = snapshots["in_memory"]
    detected = []
    for case in cases:
        candidate = copy.deepcopy(reference_snapshots[case.case_id])
        _inject_difference(case.case_id, candidate)
        differences = compare_snapshots(
            reference_snapshots[case.case_id],
            candidate,
            reference_backend="in_memory",
            candidate_backend="injected_fault",
            allowed_diff=case.allowed_diff,
        )
        detected.append(any(not difference["allowed"] for difference in differences))
    assert detected == [True] * 10


@pytest.mark.parametrize(
    ("case_id", "fault"),
    [
        ("summary_create", "missing"),
        ("summary_update", "overwrite"),
        ("summary_truncation", "ownership"),
    ],
)
def test_summary_integrity_faults_are_detected(replay_result, case_id, fault):
    cases, snapshots, _ = replay_result
    case = next(item for item in cases if item.case_id == case_id)
    reference = snapshots["in_memory"][case_id]
    candidate = copy.deepcopy(reference)
    if fault == "missing":
        candidate["summary"]["current"] = None
    elif fault == "overwrite":
        candidate["summary"]["current"]["content"] = candidate["summary"]["history"][0]["content"]
    else:
        candidate["summary"]["current"]["session_id"] = "replay-wrong-session"

    differences = compare_snapshots(
        reference,
        candidate,
        reference_backend="in_memory",
        candidate_backend=f"injected_{fault}",
        allowed_diff=case.allowed_diff,
    )
    assert any(not difference["allowed"] for difference in differences)
    assert all(difference["session_id"] == reference["session_id"] for difference in differences)
    assert any(difference["summary_id"] for difference in differences)
