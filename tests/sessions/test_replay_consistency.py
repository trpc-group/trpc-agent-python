#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Acceptance tests for the Session / Memory / Summary replay harness."""

from __future__ import annotations

import ast
import asyncio
import copy
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from .replay_cases import APP_NAME
from .replay_cases import BASE_TIMESTAMP
from .replay_cases import ExpectedOutcome
from .replay_cases import LARGE_INTEGER_VALUE
from .replay_cases import OperationKind
from .replay_cases import REPLAY_CASES
from .replay_cases import ReplayCase
from .replay_cases import ReplayOperation
from .replay_cases import SESSION_ID
from .replay_cases import USER_ID
from .replay_cases import validate_replay_cases
from .replay_harness import ReplayRunner
from .replay_harness import ReplayIdentity
from .replay_harness import _clone_operation
from .replay_harness import create_in_memory_backend
from .replay_harness import create_redis_backend
from .replay_harness import create_sqlite_backend
from .replay_harness import validate_snapshot
from .replay_report import DiffItem
from .replay_report import build_report
from .replay_report import compare_snapshots
from .replay_report import compare_persisted_snapshots
from .replay_report import comparison_record
from .replay_report import mutate_snapshot
from .replay_report import run_record
from .replay_report import write_report

LIGHTWEIGHT_TIMEOUT_SECONDS = 30.0
CONCURRENT_SESSION_COUNT = 10
CONCURRENT_ROUNDS = 5
MINIMUM_CASE_COUNT = 10
EXPECTED_MUTATION_COUNT = 11
REPORT_PATH = Path(__file__).parent / "session_memory_summary_diff_report.json"
INJECTED_OPERATION_INDEX = 99
BACKEND_RUNS_PER_CASE = 3
COMPARISONS_PER_CASE = 2
INVALID_FAILURE_COUNT = 2
INVALID_SUMMARY_GENERATION = 2
INVALID_ANCHOR_COUNT = 2
INVALID_HISTORICAL_MINIMUM = 100
MINIMUM_EXPECTATION_ERRORS = 8
MAX_FALSE_POSITIVE_RATE = 0.05
MAX_FUNCTION_LINES = 80
MAX_FUNCTION_STATEMENTS = 60
MAX_FUNCTION_PARAMETERS = 4
MAX_FILE_LINES = 1000
STRUCTURAL_NUMBERS = {-1, 0, 1}
REDIS_ENVIRONMENT_VARIABLE = "TRPC_REPLAY_REDIS_URL"
BACKEND_MODE_ENVIRONMENT_VARIABLE = "TRPC_REPLAY_BACKENDS"
IN_MEMORY_BACKEND_MODE = "in_memory"
QUALITY_FILES = (
    "replay_harness.py",
    "replay_report.py",
    "replay_cases.py",
    "test_replay_consistency.py",
    "test_replay_real_agent.py",
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def replay_matrix(tmp_path_factory):
    """Run all public cases once and retain detached snapshots."""
    validate_replay_cases(REPLAY_CASES)
    root = tmp_path_factory.mktemp("replay-matrix")
    matrix: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    for replay_case in REPLAY_CASES:
        matrix[replay_case.case_id] = await _run_backend_pair(replay_case, root)
    matrix["_duration"] = {"seconds": time.perf_counter() - started}
    return matrix


async def _run_backend_pair(replay_case, root: Path) -> dict[str, Any]:
    in_memory = None
    sqlite = None
    reopened = None
    db_path = root / f"{replay_case.case_id}.db"
    try:
        in_memory = await create_in_memory_backend()
        sqlite = await create_sqlite_backend(db_path)
        in_memory_started = time.perf_counter()
        in_memory_snapshot = await ReplayRunner(in_memory).run(replay_case)
        in_memory_duration = time.perf_counter() - in_memory_started
        sqlite_started = time.perf_counter()
        sqlite_snapshot = await ReplayRunner(sqlite).run(replay_case)
        await sqlite.close()
        sqlite = None
        reopened = await create_sqlite_backend(db_path)
        reloaded_snapshot = await ReplayRunner(reopened).reload_snapshot(replay_case)
        sqlite_duration = time.perf_counter() - sqlite_started
        return {
            "in_memory": in_memory_snapshot,
            "sqlite": sqlite_snapshot,
            "sqlite_reloaded": reloaded_snapshot,
            "durations": {
                "in_memory": in_memory_duration,
                "sqlite": sqlite_duration,
            },
        }
    finally:
        await asyncio.gather(*(backend.close() for backend in (in_memory, sqlite, reopened) if backend is not None))


@pytest.mark.parametrize("case_id", [case.case_id for case in REPLAY_CASES])
def test_public_replay_case_is_consistent(replay_matrix, case_id):
    pair = replay_matrix[case_id]
    live_diffs = compare_snapshots(pair["in_memory"], pair["sqlite"])
    persisted_diffs = compare_persisted_snapshots(pair["in_memory"], pair["sqlite_reloaded"])
    assert [diff for diff in [*live_diffs, *persisted_diffs] if not diff.allowed] == []


@pytest.mark.parametrize("case_id", [case.case_id for case in REPLAY_CASES])
def test_each_backend_matches_independent_expectation(replay_matrix, case_id):
    replay_case = next(case for case in REPLAY_CASES if case.case_id == case_id)
    pair = replay_matrix[case_id]
    assert validate_snapshot(replay_case.expected, pair["in_memory"]) == []
    assert validate_snapshot(replay_case.expected, pair["sqlite"]) == []
    assert validate_snapshot(
        replay_case.expected,
        pair["sqlite_reloaded"],
        require_cache=False,
        require_runtime=False,
    ) == []


def test_public_case_count_and_lightweight_runtime(replay_matrix):
    assert len(REPLAY_CASES) >= MINIMUM_CASE_COUNT
    assert replay_matrix["_duration"]["seconds"] < LIGHTWEIGHT_TIMEOUT_SECONDS


async def test_in_memory_only_lightweight_mode():
    if os.getenv(BACKEND_MODE_ENVIRONMENT_VARIABLE) != IN_MEMORY_BACKEND_MODE:
        pytest.skip(f"set {BACKEND_MODE_ENVIRONMENT_VARIABLE}={IN_MEMORY_BACKEND_MODE} to run the InMemory-only replay")
    started = time.perf_counter()
    failures = []
    for replay_case in REPLAY_CASES:
        backend = await create_in_memory_backend()
        try:
            snapshot = await ReplayRunner(backend).run(replay_case)
            failures.extend(f"{replay_case.case_id}: {error}"
                            for error in validate_snapshot(replay_case.expected, snapshot))
        finally:
            await backend.close()
    assert failures == []
    assert time.perf_counter() - started < LIGHTWEIGHT_TIMEOUT_SECONDS


def test_duplicate_replay_case_ids_are_rejected():
    duplicate = ReplayCase(REPLAY_CASES[0].case_id, (), ExpectedOutcome(()))
    with pytest.raises(ValueError, match="duplicate replay case ids"):
        validate_replay_cases((REPLAY_CASES[0], duplicate))


def test_operation_clone_isolates_nested_payloads():
    original = ReplayOperation(OperationKind.APPEND, {"nested": {"values": ["original"]}})
    isolated = _clone_operation(original)
    isolated.payload["nested"]["values"].append("mutated")
    assert original.payload["nested"]["values"] == ["original"]


async def test_physical_session_scope_mismatch_is_rejected():
    backend = await create_in_memory_backend()
    try:
        session = await backend.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
        )
        backend.session_service.get_session = AsyncMock(return_value=session.model_copy(
            update={"app_name": "wrong-app"}))
        with pytest.raises(AssertionError, match="physical session scope mismatch"):
            await ReplayRunner(backend)._read_session()
    finally:
        await backend.close()


async def test_repeated_memory_search_keeps_point_in_time_results():
    backend = await create_in_memory_backend()
    runner = ReplayRunner(backend)
    first_result = object()
    second_result = object()
    backend.memory_service.search_memory = AsyncMock(side_effect=(first_result, second_result))
    operation = ReplayOperation(OperationKind.SEARCH_MEMORY, {"query": "preference"})
    try:
        runner.session = await backend.session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
        )
        await runner._search_memory(operation, 0)
        await runner._search_memory(operation, 1)
        assert runner.memory_results == {
            "preference": first_result,
            "preference#2": second_result,
        }
    finally:
        await backend.close()


async def test_reloaded_memory_uses_final_read_without_rewriting_trace_result(tmp_path):
    replay_case = ReplayCase(
        "point-in-time-memory",
        (
            ReplayOperation(OperationKind.CREATE),
            ReplayOperation(
                OperationKind.APPEND,
                {
                    "event_id": "memory-before",
                    "author": "user",
                    "text": "jasmine before search",
                },
            ),
            ReplayOperation(OperationKind.STORE_MEMORY),
            ReplayOperation(OperationKind.SEARCH_MEMORY, {"query": "jasmine"}),
            ReplayOperation(
                OperationKind.APPEND,
                {
                    "event_id": "memory-after",
                    "author": "user",
                    "text": "jasmine after search",
                },
            ),
            ReplayOperation(OperationKind.STORE_MEMORY),
        ),
        ExpectedOutcome(("memory-before", "memory-after")),
    )
    db_path = tmp_path / "point-in-time-memory.db"
    backend = await create_sqlite_backend(db_path)
    live = await ReplayRunner(backend).run(replay_case)
    await backend.close()
    reopened = await create_sqlite_backend(db_path)
    try:
        reloaded = await ReplayRunner(reopened).reload_snapshot(replay_case)
        assert len(live["memory"]["jasmine"]["memories"]) < len(live["memory_final"]["jasmine"]["memories"])
        assert reloaded["memory"] == {}
        assert [diff for diff in compare_persisted_snapshots(live, reloaded) if not diff.allowed] == []
    finally:
        await reopened.close()


def test_large_integer_survives_each_backend(replay_matrix):
    snapshots = replay_matrix["state_overwrite"]
    for backend in ("in_memory", "sqlite", "sqlite_reloaded"):
        assert snapshots[backend]["state"]["large_counter"] == LARGE_INTEGER_VALUE


def test_report_rejects_duplicate_identities_and_unused_rules():
    duplicate_run = {"case_id": "duplicate", "backend": "in_memory"}
    with pytest.raises(ValueError, match="duplicate report identities"):
        build_report([duplicate_run, dict(duplicate_run)], [])
    comparison = comparison_record("unused-rule", ("in_memory", "sqlite_reloaded"), [])
    with pytest.raises(ValueError, match="unused allowed-diff rules"):
        build_report([], [comparison])


async def test_report_contains_every_backend_and_case(replay_matrix, tmp_path):
    runs = []
    comparisons = []
    for replay_case in REPLAY_CASES:
        pair = replay_matrix[replay_case.case_id]
        for backend in ("in_memory", "sqlite", "sqlite_reloaded"):
            duration_key = "sqlite" if backend == "sqlite_reloaded" else backend
            runs.append(run_record(pair[backend], pair["durations"][duration_key]))
        live_diffs = compare_snapshots(pair["in_memory"], pair["sqlite"])
        persisted_diffs = compare_persisted_snapshots(pair["in_memory"], pair["sqlite_reloaded"])
        comparisons.append(comparison_record(replay_case.case_id, ("in_memory", "sqlite"), live_diffs))
        comparisons.append(comparison_record(replay_case.case_id, ("in_memory", "sqlite_reloaded"), persisted_diffs))
    redis_enabled = bool(os.getenv(REDIS_ENVIRONMENT_VARIABLE))
    if redis_enabled:
        await _append_redis_report_data(runs, comparisons)
    detection = _mutation_results(replay_matrix)
    report = build_report(
        runs,
        comparisons,
        skips=[{
            "backend": "redis",
            "reason": f"{REDIS_ENVIRONMENT_VARIABLE} is not set",
        }] if not os.getenv(REDIS_ENVIRONMENT_VARIABLE) else [],
        detection_metrics={
            "mutation_count": len(detection),
            "mutation_detected": sum(detection.values()),
            "mutation_detection_rate": sum(detection.values()) / len(detection),
            "summary_lost_detection_rate": float(detection["summary_lost"]),
            "summary_overwrite_detection_rate": float(detection["summary_overwrite"]),
            "summary_session_detection_rate": float(detection["summary_session"]),
        },
    )
    target = tmp_path / "session_memory_summary_diff_report.json"
    write_report(report, target)
    persisted = __import__("json").loads(target.read_text(encoding="utf-8"))
    assert persisted["metrics"]["case_count"] == len(REPLAY_CASES)
    assert persisted["metrics"]["false_positive_rate"] <= MAX_FALSE_POSITIVE_RATE
    assert persisted["metrics"]["mutation_detection_rate"] == 1.0
    assert len(persisted["runs"]) == len(REPLAY_CASES) * (BACKEND_RUNS_PER_CASE + int(redis_enabled))
    assert len(persisted["comparisons"]) == len(REPLAY_CASES) * (COMPARISONS_PER_CASE + int(redis_enabled))
    assert persisted["allowed_diff"]
    assert all(item["hit_count"] > 0 for item in persisted["allowed_diff_audit"])
    assert persisted["normalization_rules"]
    if not redis_enabled:
        root_report = __import__("json").loads(REPORT_PATH.read_text(encoding="utf-8"))
        assert _stable_report(root_report) == _stable_report(persisted)


async def _append_redis_report_data(runs, comparisons):
    for replay_case in REPLAY_CASES:
        namespace = uuid.uuid4().hex
        identity = ReplayIdentity(
            app_name=f"{APP_NAME}-{namespace}",
            user_id=f"{USER_ID}-{namespace}",
            session_id=f"{SESSION_ID}-{namespace}",
        )
        in_memory = None
        backend = None
        try:
            in_memory = await create_in_memory_backend(identity)
            backend = await create_redis_backend(os.environ[REDIS_ENVIRONMENT_VARIABLE], identity)
            expected = await ReplayRunner(in_memory).run(replay_case)
            started = time.perf_counter()
            snapshot = await ReplayRunner(backend).run(replay_case)
            runs.append(run_record(snapshot, time.perf_counter() - started))
            diffs = compare_snapshots(expected, snapshot)
            comparisons.append(comparison_record(replay_case.case_id, ("in_memory", "redis"), diffs))
        finally:
            await asyncio.gather(*(item.close() for item in (in_memory, backend) if item is not None))


def _stable_report(report):
    stable = copy.deepcopy(report)
    for run in stable["runs"]:
        run["duration_seconds"] = 0
    return stable


async def test_sqlite_summary_anchor_survives_close_and_reopen(tmp_path):
    replay_case = next(case for case in REPLAY_CASES if case.case_id == "summary_update")
    db_path = tmp_path / "reopen.db"
    backend = await create_sqlite_backend(db_path)
    try:
        live = await ReplayRunner(backend).run(replay_case)
    finally:
        await backend.close()
    reopened = await create_sqlite_backend(db_path)
    try:
        runner = ReplayRunner(reopened)
        runner.session = await reopened.session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
        )
        runner.summary_generation = live["summary"]["generation"]
        restored = await runner.snapshot(replay_case.case_id)
    finally:
        await reopened.close()
    assert restored["summary"]["cache_present"] is False
    assert restored["summary"]["session_id"] == SESSION_ID
    assert restored["summary"]["text"] == live["summary"]["text"]
    assert restored["summary"]["anchor_count"] == 1


@dataclass(frozen=True)
class MutationSpec:
    """One injected inconsistency and its expected path."""

    name: str
    case_id: str
    expected_path: str
    mutate: Callable[[dict[str, Any]], None]


def _set_event_author(snapshot):
    snapshot["events"][0]["author"] = "intruder"


def _reverse_events(snapshot):
    snapshot["events"][0], snapshot["events"][1] = snapshot["events"][1], snapshot["events"][0]


def _change_state(snapshot):
    snapshot["state"]["theme"] = "corrupted"


def _change_tool_name(snapshot):
    snapshot["events"][1]["content"]["parts"][0]["function_call"]["name"] = "wrong-tool"


def _remove_memory(snapshot):
    snapshot["memory"]["jasmine"]["memories"].pop()


def _lose_summary(snapshot):
    snapshot["summary"] = None


def _restore_old_summary(snapshot):
    old_summary = copy.deepcopy(snapshot["summary_checkpoints"][0])
    snapshot["summary"].update(old_summary)
    snapshot["events"][0] = copy.deepcopy(old_summary["anchor"])


def _move_summary_session(snapshot):
    snapshot["summary"]["session_id"] = "wrong-session"


def _reverse_summary_time(snapshot):
    snapshot["summary"]["updated_at"] = -1.0


def _duplicate_event(snapshot):
    snapshot["events"].append(copy.deepcopy(snapshot["events"][0]))


def _add_failure(snapshot):
    snapshot["failures"].append({
        "operation_index": INJECTED_OPERATION_INDEX,
        "error_type": "InjectedReplayFailure",
        "message": "unexpected",
    })


MUTATIONS = (
    MutationSpec("event_content", "single_turn", "/events/0/author", _set_event_author),
    MutationSpec("event_order", "multi_turn", "/events/0/author", _reverse_events),
    MutationSpec("tool_name", "tool_round_trip", "/events/1/content/parts/0/function_call/name", _change_tool_name),
    MutationSpec("state_value", "state_overwrite", "/state/theme", _change_state),
    MutationSpec("memory_missing", "memory_preference", "/memory/jasmine/memories/1", _remove_memory),
    MutationSpec("summary_lost", "summary_create", "/summary", _lose_summary),
    MutationSpec("summary_overwrite", "summary_update", "/summary/generation", _restore_old_summary),
    MutationSpec("summary_time", "summary_update", "/summary/updated_at/not_before_checkpoints", _reverse_summary_time),
    MutationSpec("summary_session", "summary_truncation", "/summary/session_id", _move_summary_session),
    MutationSpec("event_duplicate", "duplicate_retry", "/events/1", _duplicate_event),
    MutationSpec("dirty_failure", "write_recovery", "/failures/1", _add_failure),
)


@pytest.mark.parametrize("spec", MUTATIONS, ids=lambda spec: spec.name)
def test_injected_inconsistency_is_detected(replay_matrix, spec):
    pair = replay_matrix[spec.case_id]
    mutated = mutate_snapshot(pair["sqlite"], spec.mutate)
    diffs = compare_snapshots(pair["in_memory"], mutated)
    unexpected_paths = {diff.field_path for diff in diffs if not diff.allowed}
    assert spec.expected_path in unexpected_paths


def test_mutation_and_required_summary_detection_rates(replay_matrix):
    detected = _mutation_results(replay_matrix)
    assert len(MUTATIONS) == EXPECTED_MUTATION_COUNT
    assert all(detected.values())
    required = {"summary_lost", "summary_overwrite", "summary_session"}
    assert all(detected[name] for name in required)


def _mutation_results(replay_matrix) -> dict[str, bool]:
    results = {}
    for spec in MUTATIONS:
        pair = replay_matrix[spec.case_id]
        mutated = mutate_snapshot(pair["sqlite"], spec.mutate)
        diffs = compare_snapshots(pair["in_memory"], mutated)
        results[spec.name] = any(diff.field_path == spec.expected_path and not diff.allowed for diff in diffs)
    return results


def test_diff_localizes_event_and_summary(replay_matrix):
    event_pair = replay_matrix["single_turn"]
    event_diffs = compare_snapshots(
        event_pair["in_memory"],
        mutate_snapshot(event_pair["sqlite"], _set_event_author),
    )
    event_diff = _find_diff(event_diffs, "/events/0/author")
    assert event_diff.event_index == 0
    assert event_diff.session_id == SESSION_ID
    summary_pair = replay_matrix["summary_update"]
    summary_diffs = compare_snapshots(
        summary_pair["in_memory"],
        mutate_snapshot(summary_pair["sqlite"], _move_summary_session),
    )
    summary_diff = _find_diff(summary_diffs, "/summary/session_id")
    assert summary_diff.summary_id == "<summary-anchor>"
    assert summary_diff.left != summary_diff.right
    checkpoint_diffs = compare_persisted_snapshots(
        summary_pair["in_memory"],
        summary_pair["sqlite_reloaded"],
    )
    checkpoint_diff = _find_diff(checkpoint_diffs, "/summary_checkpoints/0")
    assert checkpoint_diff.summary_id == "<summary-anchor>:0"


def test_normalization_preserves_caller_ids_and_event_text(replay_matrix):
    pair = replay_matrix["single_turn"]
    changed_id = mutate_snapshot(pair["sqlite"], lambda snapshot: snapshot["events"][0].update(id="wrong-id"))
    changed_text = mutate_snapshot(
        pair["sqlite"],
        lambda snapshot: snapshot["events"][0]["content"]["parts"][0].update(text="Hello   replay"),
    )
    assert _find_diff(compare_snapshots(pair["in_memory"], changed_id), "/events/0/id")
    assert _find_diff(compare_snapshots(pair["in_memory"], changed_text), "/events/0/content/parts/0/text")


def test_normalization_detects_invalid_event_timestamp(replay_matrix):
    pair = replay_matrix["single_turn"]
    mutated = mutate_snapshot(pair["sqlite"], lambda snapshot: snapshot["events"][0].update(timestamp=-1.0))
    diffs = compare_snapshots(pair["in_memory"], mutated)
    assert _find_diff(diffs, "/timeline_constraints/events_all_valid")


def test_independent_expectation_reports_each_contract_failure(replay_matrix):
    snapshot = copy.deepcopy(replay_matrix["summary_update"]["in_memory"])
    snapshot["events"] = []
    snapshot["state"]["temp:leak"] = True
    snapshot["summary"]["generation"] = 0
    snapshot["summary"]["text"] = "missing"
    snapshot["summary"]["session_id"] = "wrong"
    snapshot["summary"]["anchor_count"] = INVALID_ANCHOR_COUNT
    snapshot["summary"]["cache_present"] = False
    snapshot["summary_checkpoints"] = []
    expected = ExpectedOutcome(
        ("required-event", ),
        {"required-state": "value"},
        {"required-query": 1},
        summary_generation=INVALID_SUMMARY_GENERATION,
        summary_fact="required fact",
        minimum_historical_events=INVALID_HISTORICAL_MINIMUM,
        failure_count=INVALID_FAILURE_COUNT,
    )
    errors = validate_snapshot(expected, snapshot)
    assert len(errors) >= MINIMUM_EXPECTATION_ERRORS
    assert "unexpected summary" in validate_snapshot(ExpectedOutcome(tuple()), snapshot)


def _find_diff(diffs: list[DiffItem], path: str) -> DiffItem:
    return next(diff for diff in diffs if diff.field_path == path)


async def test_sqlite_transaction_failure_rolls_back_event_and_state(tmp_path):
    backend = await create_sqlite_backend(tmp_path / "rollback.db")
    session = await backend.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
        state={"status": "clean"},
    )
    original_commit = backend.session_service._sql_storage.commit
    backend.session_service._sql_storage.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    event = Event(
        id="rollback-event",
        invocation_id="rollback",
        author="agent",
        content=Content(parts=[Part.from_text(text="dirty")]),
        timestamp=BASE_TIMESTAMP,
    )
    event.actions.state_delta = {"status": "dirty"}
    with pytest.raises(RuntimeError, match="commit failed"):
        await backend.session_service.append_event(session, event)
    backend.session_service._sql_storage.commit = original_commit
    await backend.close()
    reopened = await create_sqlite_backend(tmp_path / "rollback.db")
    try:
        restored = await reopened.session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
        )
        assert restored.state["status"] == "clean"
        assert restored.events == []
    finally:
        await reopened.close()


async def test_sqlite_summary_commit_failure_exposes_cache_storage_mismatch(tmp_path):
    db_path = tmp_path / "summary-rollback.db"
    backend = await create_sqlite_backend(db_path)
    session = await backend.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID,
    )
    for index, text in enumerate(("old fact", "old answer", "recent request")):
        event = Event(
            id=f"summary-rollback-{index}",
            invocation_id=f"summary-rollback-{index}",
            author="user" if index != 1 else "agent",
            content=Content(parts=[Part.from_text(text=text)]),
            timestamp=time.time() + index,
        )
        await backend.session_service.append_event(session, event)
    original_commit = backend.session_service._sql_storage.commit
    backend.session_service._sql_storage.commit = AsyncMock(side_effect=RuntimeError("summary commit failed"))
    with pytest.raises(RuntimeError, match="summary commit failed"):
        await backend.session_service.create_session_summary(session)
    cached = await backend.session_service.summarizer_manager.get_session_summary(session)
    assert cached is not None
    backend.session_service._sql_storage.commit = original_commit
    await backend.close()
    reopened = await create_sqlite_backend(db_path)
    try:
        restored = await reopened.session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
        )
        assert not any(event.is_summary_event() for event in restored.events)
    finally:
        await reopened.close()


async def test_reload_snapshot_clears_reused_runner_state(tmp_path):
    source = next(case for case in REPLAY_CASES if case.case_id == "write_recovery")
    summary_source = next(case for case in REPLAY_CASES if case.case_id == "summary_create")
    summary_events = tuple(operation for operation in summary_source.operations
                           if operation.kind == OperationKind.APPEND)
    replay_case = ReplayCase(
        "reload-reused-runner",
        source.operations + summary_events + (ReplayOperation(OperationKind.SUMMARY), ),
        source.expected,
    )
    backend = await create_sqlite_backend(tmp_path / "reused-runner.db")
    try:
        runner = ReplayRunner(backend)
        live = await runner.run(replay_case)
        assert live["memory"]
        assert live["summary_checkpoints"]
        assert live["failures"]
        reloaded = await runner.reload_snapshot(replay_case)
    finally:
        await backend.close()
    assert live["memory"]
    assert live["summary_checkpoints"]
    assert live["failures"]
    assert reloaded["memory"] == {}
    assert reloaded["memory_final"]
    assert reloaded["summary_checkpoints"] == []
    assert reloaded["failures"] == []
    assert reloaded["summary"]["generation"] == 0


async def test_unknown_outcome_retry_writes_event_once(tmp_path):
    replay_case = next(case for case in REPLAY_CASES if case.case_id == "duplicate_retry")
    backend = await create_sqlite_backend(tmp_path / "unknown.db")
    try:
        append_event = AsyncMock(wraps=backend.session_service.append_event)
        backend.session_service.append_event = append_event
        runner = ReplayRunner(backend)
        snapshot = await runner.run(replay_case)
        memory = await backend.memory_service.search_memory(runner.session.save_key, "Write")
    finally:
        await backend.close()
    ids = [event["id"] for event in [*snapshot["events"], *snapshot["historical_events"]]]
    assert append_event.await_count == 1
    assert ids.count("retry-event") == 1
    assert snapshot["summary"]["anchor_count"] == 1
    assert len(memory.memories) == 1


async def test_concurrent_sessions_remain_isolated():
    for _ in range(CONCURRENT_ROUNDS):
        service = InMemorySessionService()
        try:
            await asyncio.gather(
                *[_write_isolated_session(service, index) for index in range(CONCURRENT_SESSION_COUNT)])
            sessions = await service.list_sessions(app_name=APP_NAME, user_id=USER_ID)
            assert len(sessions.sessions) == CONCURRENT_SESSION_COUNT
        finally:
            await service.close()


async def _write_isolated_session(service, index: int) -> None:
    session_id = f"concurrent-{index}"
    session = await service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    event = Event(
        id=f"event-{index}",
        invocation_id=f"invocation-{index}",
        author="user",
        content=Content(parts=[Part.from_text(text=session_id)]),
        timestamp=BASE_TIMESTAMP + index,
    )
    await service.append_event(session, event)
    stored = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    assert stored.events[0].get_text() == session_id


def test_new_files_meet_size_and_function_limits():
    base = Path(__file__).parent
    violations = []
    for filename in QUALITY_FILES:
        path = base / filename
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        if len(lines) > MAX_FILE_LINES:
            violations.append(f"{filename}: file has {len(lines)} lines")
        tree = ast.parse(source)
        violations.extend(_function_violations(filename, tree))
    assert violations == []


def _function_violations(filename: str, tree: ast.AST) -> list[str]:
    violations = []
    function_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in (item for item in ast.walk(tree) if isinstance(item, function_types)):
        end_line = getattr(node, "end_lineno", node.lineno)
        line_count = end_line - node.lineno + 1
        statement_count = sum(isinstance(item, ast.stmt) for item in ast.walk(node))
        parameter_count = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
        parameter_count += int(node.args.vararg is not None) + int(node.args.kwarg is not None)
        if line_count > MAX_FUNCTION_LINES:
            violations.append(f"{filename}:{node.lineno} {node.name} has {line_count} lines")
        if statement_count > MAX_FUNCTION_STATEMENTS:
            violations.append(f"{filename}:{node.lineno} {node.name} has {statement_count} statements")
        if parameter_count > MAX_FUNCTION_PARAMETERS:
            violations.append(f"{filename}:{node.lineno} {node.name} has {parameter_count} parameters")
    return violations


def test_harness_has_no_unbound_magic_numbers():
    base = Path(__file__).parent
    violations = []
    for filename in QUALITY_FILES:
        tree = ast.parse((base / filename).read_text(encoding="utf-8"))
        parents = _parent_map(tree)
        for node in (item for item in ast.walk(tree)
                     if isinstance(item, ast.Constant) and isinstance(item.value, (int, float))):
            if node.value not in STRUCTURAL_NUMBERS and not _inside_named_constant(node, parents):
                violations.append(f"{filename}:{node.lineno} literal {node.value}")
    assert violations == []


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _inside_named_constant(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return False
        if isinstance(current, (ast.Assign, ast.AnnAssign)):
            targets = current.targets if isinstance(current, ast.Assign) else [current.target]
            return all(isinstance(target, ast.Name) and target.id.isupper() for target in targets)
    return False


@pytest.mark.skipif(not os.getenv(REDIS_ENVIRONMENT_VARIABLE), reason=f"{REDIS_ENVIRONMENT_VARIABLE} is not configured")
@pytest.mark.parametrize("case_id", [case.case_id for case in REPLAY_CASES])
async def test_redis_integration_uses_isolated_namespace(case_id):
    namespace = uuid.uuid4().hex
    identity = ReplayIdentity(
        app_name=f"{APP_NAME}-{namespace}",
        user_id=f"{USER_ID}-{namespace}",
        session_id=f"{SESSION_ID}-{namespace}",
    )
    replay_case = next(case for case in REPLAY_CASES if case.case_id == case_id)
    in_memory = None
    redis = None
    try:
        in_memory = await create_in_memory_backend(identity)
        redis = await create_redis_backend(os.environ[REDIS_ENVIRONMENT_VARIABLE], identity)
        expected = await ReplayRunner(in_memory).run(replay_case)
        actual = await ReplayRunner(redis).run(replay_case)
        assert [diff for diff in compare_snapshots(expected, actual) if not diff.allowed] == []
        assert validate_snapshot(replay_case.expected, actual) == []
    finally:
        await asyncio.gather(*(backend.close() for backend in (in_memory, redis) if backend is not None))
