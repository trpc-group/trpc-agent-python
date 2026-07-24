# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Mutation tests for the replay consistency framework."""

from __future__ import annotations

import asyncio
import copy
from typing import Any
from typing import Callable

import pytest

from .replay_consistency import (
    MEMORY_REPLAY_CASES,
    REPLAY_CASES,
    ReplayCase,
    ReplayBackendUnavailable,
    build_diff_report,
    build_summary_content_checks,
    build_summary_metadata_checks,
    diff_snapshots,
    run_memory_replay_case,
    run_session_replay_case,
)


SnapshotMutator = Callable[[dict[str, Any]], None]


def _run_session_replay_case_or_skip(replay_case: ReplayCase) -> dict[str, dict[str, Any]]:
    try:
        return asyncio.run(run_session_replay_case(replay_case))
    except ReplayBackendUnavailable as ex:
        pytest.skip(str(ex))


def _run_memory_replay_case_or_skip(replay_case: ReplayCase) -> dict[str, dict[str, Any]]:
    try:
        return asyncio.run(run_memory_replay_case(replay_case))
    except ReplayBackendUnavailable as ex:
        pytest.skip(str(ex))


def _session_case(case_name: str) -> ReplayCase:
    return next(replay_case for replay_case in REPLAY_CASES if replay_case.name == case_name)


def _first_text_part(snapshot: dict[str, Any]) -> dict[str, Any]:
    for collection in ("historical_events", "events"):
        for event in snapshot.get(collection, []):
            for part in event.get("parts", []):
                if "text" in part:
                    return part
    raise AssertionError("Snapshot has no text part to mutate")


def _active_summary_event(snapshot: dict[str, Any]) -> dict[str, Any]:
    for event in snapshot["events"]:
        if event["is_summary"]:
            return event
    raise AssertionError("Snapshot has no active summary event")


def _historical_summary_text(snapshot: dict[str, Any]) -> str:
    for event in snapshot["historical_events"]:
        if event["is_summary"]:
            return event["parts"][0]["text"]
    raise AssertionError("Snapshot has no historical summary event")


def _session_unallowed_diffs(
    replay_case: ReplayCase,
    clean: dict[str, Any],
    mutated: dict[str, Any],
    *,
    backend_actual: str = "mutated",
) -> list[dict[str, Any]]:
    report = _session_diff_report(
        replay_case,
        clean,
        mutated,
        backend_actual=backend_actual,
    )
    return [entry for entry in report if not entry["allowed"]]


def _session_diff_report(
    replay_case: ReplayCase,
    clean: dict[str, Any],
    mutated: dict[str, Any],
    *,
    backend_actual: str = "mutated",
) -> list[dict[str, Any]]:
    diffs = diff_snapshots(clean, mutated)
    return build_diff_report(
        case_name=replay_case.name,
        session_id=replay_case.session_id,
        backend_expected="in_memory",
        backend_actual=backend_actual,
        diffs=diffs,
        expected_snapshot=clean,
        actual_snapshot=mutated,
    )


def _summary_mismatches(
    clean: dict[str, Any],
    mutated: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    content_checks = build_summary_content_checks(clean, mutated)
    metadata_checks = build_summary_metadata_checks(clean, mutated)
    content_mismatches = [check for check in content_checks if not check["matched"]]
    metadata_mismatches = [check for check in metadata_checks if not check["matched"]]
    return content_mismatches, metadata_mismatches


def _assert_session_mutation_detected(
    replay_case: ReplayCase,
    clean: dict[str, Any],
    mutated: dict[str, Any],
) -> None:
    assert _session_unallowed_diffs(replay_case, clean, mutated)


def _assert_summary_mutation_detected(
    replay_case: ReplayCase,
    clean: dict[str, Any],
    mutated: dict[str, Any],
) -> None:
    content_mismatches, metadata_mismatches = _summary_mismatches(clean, mutated)
    assert (
        _session_unallowed_diffs(replay_case, clean, mutated)
        or content_mismatches
        or metadata_mismatches
    )


@pytest.mark.parametrize("replay_case", REPLAY_CASES, ids=lambda replay_case: replay_case.name)
def test_each_session_case_detects_a_basic_event_text_mutation(replay_case: ReplayCase):
    snapshots = _run_session_replay_case_or_skip(replay_case)
    clean = snapshots["in_memory"]
    mutated = copy.deepcopy(clean)

    _first_text_part(mutated)["text"] = "MUTATED: event text changed"

    _assert_session_mutation_detected(replay_case, clean, mutated)


def test_state_mutation_is_detected():
    replay_case = _session_case("state_update")
    snapshots = _run_session_replay_case_or_skip(replay_case)
    clean = snapshots["in_memory"]
    mutated = copy.deepcopy(clean)

    mutated["state"]["profile.name"] = "Mallory"

    _assert_session_mutation_detected(replay_case, clean, mutated)


def test_tool_call_mutation_is_detected():
    replay_case = _session_case("tool_call")
    snapshots = _run_session_replay_case_or_skip(replay_case)
    clean = snapshots["in_memory"]
    mutated = copy.deepcopy(clean)

    mutated["events"][1]["parts"][0]["function_call"]["name"] = "get_stock_price"

    _assert_session_mutation_detected(replay_case, clean, mutated)


def _drop_first_memory(snapshot: dict[str, Any]) -> None:
    snapshot["searches"][0]["memories"].pop(0)


def _change_memory_text(snapshot: dict[str, Any]) -> None:
    snapshot["searches"][0]["memories"][0]["parts"][0]["text"] = "MUTATED: wrong memory"


def _change_memory_author(snapshot: dict[str, Any]) -> None:
    snapshot["searches"][-1]["memories"][0]["author"] = "user"


@pytest.mark.parametrize(
    "mutate",
    [_drop_first_memory, _change_memory_text, _change_memory_author],
    ids=["memory_missing", "memory_text_wrong", "memory_author_wrong"],
)
def test_memory_mutations_are_detected(mutate: SnapshotMutator):
    replay_case = next(replay_case for replay_case in MEMORY_REPLAY_CASES if replay_case.name == "memory_store_search")
    snapshots = _run_memory_replay_case_or_skip(replay_case)
    clean = snapshots["in_memory"]
    mutated = copy.deepcopy(clean)

    mutate(mutated)

    assert diff_snapshots(clean, mutated)


def _drop_summary_cache(snapshot: dict[str, Any]) -> None:
    snapshot["summary"] = None


def _change_summary_text(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["text"] = "MUTATED: wrong summary"


def _change_summary_session_id(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["metadata"]["session_id"] = "wrong-session"


def _change_summary_manager_session_id(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["metadata"]["manager_session_id"] = "wrong-session"


def _overwrite_latest_summary_with_stale_summary(snapshot: dict[str, Any]) -> None:
    stale_summary_text = _historical_summary_text(snapshot)
    latest_summary_event = _active_summary_event(snapshot)
    snapshot["summary"]["text"] = stale_summary_text.removeprefix("Previous conversation summary: ")
    snapshot["summary"]["metadata"]["summary_event_text"] = stale_summary_text
    latest_summary_event["parts"][0]["text"] = stale_summary_text


def _drop_active_summary_event(snapshot: dict[str, Any]) -> None:
    snapshot["events"] = [event for event in snapshot["events"] if not event["is_summary"]]
    snapshot["summary"]["metadata"]["summary_event_count"] = 0
    snapshot["summary"]["metadata"]["summary_event_text"] = None


def _change_summary_compressed_count(snapshot: dict[str, Any]) -> None:
    snapshot["summary"]["metadata"]["compressed_event_count"] += 1


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_summary_cache,
        _change_summary_text,
        _change_summary_session_id,
        _change_summary_manager_session_id,
        _overwrite_latest_summary_with_stale_summary,
        _drop_active_summary_event,
        _change_summary_compressed_count,
    ],
    ids=[
        "summary_missing",
        "summary_text_wrong",
        "summary_session_wrong",
        "summary_manager_session_wrong",
        "summary_overwritten_by_stale_summary",
        "summary_event_missing",
        "summary_compressed_count_wrong",
    ],
)
def test_summary_mutations_are_detected(mutate: SnapshotMutator):
    replay_case = _session_case("summary_generation_update")
    snapshots = _run_session_replay_case_or_skip(replay_case)
    clean = snapshots["in_memory"]
    mutated = copy.deepcopy(clean)

    mutate(mutated)

    _assert_summary_mutation_detected(replay_case, clean, mutated)


def test_allowed_summary_event_order_diff_does_not_hide_summary_metadata_mismatch():
    replay_case = _session_case("summary_generation_update")
    snapshots = _run_session_replay_case_or_skip(replay_case)
    clean = snapshots["in_memory"]
    mutated = copy.deepcopy(clean)

    latest_summary_event = _active_summary_event(mutated)
    latest_summary_event["parts"][0]["text"] = "MUTATED: wrong summary event text"
    mutated["summary"]["metadata"]["summary_event_text"] = "MUTATED: wrong summary event text"

    content_mismatches, metadata_mismatches = _summary_mismatches(clean, mutated)
    report = _session_diff_report(
        replay_case,
        clean,
        mutated,
        backend_actual="sqlite_sql",
    )
    allowed_paths = {entry["field_path"] for entry in report if entry["allowed"]}
    unallowed_paths = {entry["field_path"] for entry in report if not entry["allowed"]}

    assert "events[0].parts[0].text" in allowed_paths
    assert "summary.metadata.summary_event_text" in unallowed_paths
    assert content_mismatches == []
    assert metadata_mismatches
