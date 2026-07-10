# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency tests for session and memory backends."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from .replay_consistency import (
    BASELINE_BACKEND_NAME,
    ENV_REDIS_BACKEND_NAME,
    ENV_SQL_BACKEND_NAME,
    MOCK_REDIS_BACKEND_NAME,
    REPLAY_CASES,
    MEMORY_REPLAY_CASES,
    REDIS_URL_ENV,
    SQLITE_BACKEND_NAME,
    SQL_URL_ENV,
    ReplayCase,
    ReplayBackendConfig,
    ReplayBackendUnavailable,
    comparison_backend_names,
    configured_memory_backend_names,
    configured_session_backend_names,
    default_backend_matrix_enabled,
    run_session_replay_case,
    run_memory_replay_case,
    diff_snapshots,
    build_replay_diff_report,
    build_diff_report,
    build_summary_content_checks,
    build_summary_metadata_checks,
    assert_all_diffs_allowed,
    assert_session_replay_case_snapshot,
    assert_memory_replay_case_snapshot,
    assert_replay_case_fixtures_load,
    uses_allowed_snapshot_variant,
    assert_allowed_session_snapshot_variant,
)


def test_replay_case_fixtures_load():
    assert_replay_case_fixtures_load()


def test_replay_backend_matrix_defaults_to_in_memory(monkeypatch):
    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)

    assert configured_session_backend_names() == [BASELINE_BACKEND_NAME]
    assert configured_memory_backend_names() == [BASELINE_BACKEND_NAME]


def test_replay_backend_matrix_uses_configured_env_backends(monkeypatch):
    monkeypatch.setenv(SQL_URL_ENV, "sqlite:///:memory:")
    monkeypatch.setenv(REDIS_URL_ENV, "redis://localhost:6379/15")

    assert configured_session_backend_names() == [
        BASELINE_BACKEND_NAME,
        ENV_SQL_BACKEND_NAME,
        ENV_REDIS_BACKEND_NAME,
    ]
    assert configured_memory_backend_names() == [
        BASELINE_BACKEND_NAME,
        ENV_SQL_BACKEND_NAME,
        ENV_REDIS_BACKEND_NAME,
    ]


def test_replay_backend_matrix_uses_single_configured_env_backend(monkeypatch):
    monkeypatch.setenv(SQL_URL_ENV, "sqlite:///:memory:")
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)

    assert configured_session_backend_names() == [BASELINE_BACKEND_NAME, ENV_SQL_BACKEND_NAME]
    assert configured_memory_backend_names() == [BASELINE_BACKEND_NAME, ENV_SQL_BACKEND_NAME]

    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.setenv(REDIS_URL_ENV, "redis://localhost:6379/15")

    assert configured_session_backend_names() == [BASELINE_BACKEND_NAME, ENV_REDIS_BACKEND_NAME]
    assert configured_memory_backend_names() == [BASELINE_BACKEND_NAME, ENV_REDIS_BACKEND_NAME]


def test_unconfigured_replay_runs_only_in_memory(monkeypatch):
    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)
    replay_case = next(replay_case for replay_case in REPLAY_CASES if replay_case.name == "single_turn")

    snapshots = asyncio.run(run_session_replay_case(replay_case))

    assert list(snapshots) == [BASELINE_BACKEND_NAME]


def test_unavailable_sql_backend_falls_back_to_sqlite_sql(monkeypatch):
    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)
    replay_case = next(replay_case for replay_case in REPLAY_CASES if replay_case.name == "single_turn")

    snapshots = asyncio.run(run_session_replay_case(
        replay_case,
        backend_config=ReplayBackendConfig(sql_url="not-a-valid-sql-url"),
    ))

    assert list(snapshots) == [BASELINE_BACKEND_NAME, SQLITE_BACKEND_NAME]


def test_unavailable_redis_backend_falls_back_to_mock_redis(monkeypatch):
    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)
    replay_case = next(replay_case for replay_case in REPLAY_CASES if replay_case.name == "single_turn")

    snapshots = asyncio.run(run_session_replay_case(
        replay_case,
        backend_config=ReplayBackendConfig(redis_url="redis://localhost:1/15"),
    ))

    assert list(snapshots) == [BASELINE_BACKEND_NAME, MOCK_REDIS_BACKEND_NAME]


def test_unavailable_sql_memory_backend_falls_back_to_sqlite_sql(monkeypatch):
    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)
    replay_case = next(replay_case for replay_case in MEMORY_REPLAY_CASES if replay_case.name == "memory_store_search")

    snapshots = asyncio.run(run_memory_replay_case(
        replay_case,
        backend_config=ReplayBackendConfig(sql_url="not-a-valid-sql-url"),
    ))

    assert list(snapshots) == [BASELINE_BACKEND_NAME, SQLITE_BACKEND_NAME]


def test_unavailable_redis_memory_backend_falls_back_to_mock_redis(monkeypatch):
    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)
    replay_case = next(replay_case for replay_case in MEMORY_REPLAY_CASES if replay_case.name == "memory_store_search")

    snapshots = asyncio.run(run_memory_replay_case(
        replay_case,
        backend_config=ReplayBackendConfig(redis_url="redis://localhost:1/15"),
    ))

    assert list(snapshots) == [BASELINE_BACKEND_NAME, MOCK_REDIS_BACKEND_NAME]


@pytest.mark.parametrize("replay_case", REPLAY_CASES, ids=lambda replay_case: replay_case.name)
def test_session_replay_case_is_consistent_between_configured_backends(replay_case: ReplayCase):
    try:
        snapshots = asyncio.run(run_session_replay_case(replay_case))
    except ReplayBackendUnavailable as ex:
        pytest.skip(str(ex))

    backend_names = comparison_backend_names(snapshots)
    if not backend_names:
        pytest.skip("Replay backend config has no persistent session backend to compare")

    for backend_name in backend_names:
        diffs = diff_snapshots(snapshots[BASELINE_BACKEND_NAME], snapshots[backend_name])
        if diffs:
            report = build_diff_report(
                case_name=replay_case.name,
                session_id=replay_case.session_id,
                backend_expected=BASELINE_BACKEND_NAME,
                backend_actual=backend_name,
                diffs=diffs,
                expected_snapshot=snapshots[BASELINE_BACKEND_NAME],
                actual_snapshot=snapshots[backend_name],
            )
            assert_all_diffs_allowed(report)
            continue

        assert diffs == []


@pytest.mark.parametrize("replay_case", REPLAY_CASES, ids=lambda replay_case: replay_case.name)
def test_session_replay_case_preserves_expected_event_trace(replay_case: ReplayCase):
    try:
        snapshots = asyncio.run(run_session_replay_case(replay_case))
    except ReplayBackendUnavailable as ex:
        pytest.skip(str(ex))

    for backend_name, snapshot in snapshots.items():
        if uses_allowed_snapshot_variant(replay_case.name, backend_name):
            assert_allowed_session_snapshot_variant(replay_case.name, snapshot)
            continue
        assert_session_replay_case_snapshot(replay_case.name, snapshot)


@pytest.mark.parametrize("replay_case", MEMORY_REPLAY_CASES, ids=lambda replay_case: replay_case.name)
def test_memory_replay_case_is_consistent_between_configured_backends(replay_case: ReplayCase):
    try:
        snapshots = asyncio.run(run_memory_replay_case(replay_case))
    except ReplayBackendUnavailable as ex:
        pytest.skip(str(ex))

    backend_names = comparison_backend_names(snapshots)
    if not backend_names:
        pytest.skip("Replay backend config has no persistent memory backend to compare")

    for backend_name in backend_names:
        diffs = diff_snapshots(snapshots[BASELINE_BACKEND_NAME], snapshots[backend_name])
        assert diffs == []
    for snapshot in snapshots.values():
        assert_memory_replay_case_snapshot(replay_case, snapshot)


def test_diff_snapshots_reports_nested_event_text_mismatch():
    baseline = {
        "session_id": "s1",
        "events": [
            {"author": "user", "parts": [{"text": "hello"}]},
            {"author": "agent", "parts": [{"text": "hi"}]},
        ],
    }
    actual = {
        "session_id": "s1",
        "events": [
            {"author": "user", "parts": [{"text": "hello"}]},
            {"author": "agent", "parts": [{"text": "wrong"}]},
        ],
    }

    diffs = diff_snapshots(baseline, actual)

    assert diffs == [{
        "field_path": "events[1].parts[0].text",
        "expected": "hi",
        "actual": "wrong",
    }]


def test_diff_report_entries_include_context_and_allowed_diff_metadata():
    expected_snapshot = {
        "session_id": "session-summary-truncation",
        "events": [{"author": "user", "parts": [{"text": "My name is Alice."}]}],
        "historical_events": [],
    }
    actual_snapshot = {
        "session_id": "session-summary-truncation",
        "events": [{
            "author": "system",
            "is_summary": True,
            "custom_metadata": {"summary_id": "summary-session-summary-truncation-v1"},
            "parts": [{"text": "Summary: Alice introduced herself."}],
        }],
        "historical_events": [],
    }
    diffs = diff_snapshots(expected_snapshot, actual_snapshot)

    report = build_diff_report(
        case_name="summary_truncation",
        session_id="session-summary-truncation",
        backend_expected="in_memory",
        backend_actual="sqlite_sql",
        diffs=diffs,
        expected_snapshot=expected_snapshot,
        actual_snapshot=actual_snapshot,
    )
    text_diff = next(entry for entry in report if entry["field_path"] == "events[0].parts[0].text")

    assert text_diff["case_name"] == "summary_truncation"
    assert text_diff["session_id"] == "session-summary-truncation"
    assert text_diff["backend_expected"] == "in_memory"
    assert text_diff["backend_actual"] == "sqlite_sql"
    assert text_diff["event_collection"] == "events"
    assert text_diff["event_index"] == 0
    assert text_diff["summary_id"] == "summary-session-summary-truncation-v1"
    assert text_diff["expected"] == "My name is Alice."
    assert text_diff["actual"] == "Summary: Alice introduced herself."
    assert text_diff["allowed"] is True
    assert "InMemorySessionService" in text_diff["allowed_reason"]


def test_diff_report_marks_unlisted_backend_difference_as_unallowed():
    report = build_diff_report(
        case_name="summary_truncation",
        session_id="session-summary-truncation",
        backend_expected="in_memory",
        backend_actual="sqlite_sql",
        diffs=[{"field_path": "state.profile.name", "expected": "Alice", "actual": "Bob"}],
        expected_snapshot={"session_id": "session-summary-truncation", "events": [], "historical_events": []},
        actual_snapshot={"session_id": "session-summary-truncation", "events": [], "historical_events": []},
    )

    assert report == [{
        "case_name": "summary_truncation",
        "session_id": "session-summary-truncation",
        "backend_expected": "in_memory",
        "backend_actual": "sqlite_sql",
        "field_path": "state.profile.name",
        "event_collection": None,
        "event_index": None,
        "summary_id": None,
        "expected": "Alice",
        "actual": "Bob",
        "allowed": False,
        "allowed_reason": None,
    }]


def test_replay_diff_report_records_every_case_and_diff_category():
    if not default_backend_matrix_enabled():
        pytest.skip("The fixture report assertions target the default InMemory-only matrix")

    report = build_replay_diff_report()
    cases = report["cases"]

    assert report["backend_pairs"] == {
        "session": [BASELINE_BACKEND_NAME],
        "memory": [BASELINE_BACKEND_NAME],
    }
    assert [case["case_name"] for case in cases] == [replay_case.name for replay_case in REPLAY_CASES]
    for case in cases:
        assert case["session"]["backend_expected"] == BASELINE_BACKEND_NAME
        assert case["session"]["backend_actual"] is None
        assert case["session"]["backend_actuals"] == []
        assert set(case["session"]) >= {
            "status",
            "event_diffs",
            "state_diffs",
            "summary_diffs",
            "summary_content_checks",
            "summary_metadata_checks",
            "session_metadata_diffs",
        }
        assert set(case["memory"]) >= {"status", "memory_diffs"}

    summary_case = next(case for case in cases if case["case_name"] == "summary_truncation")
    assert summary_case["status"] == "matched"
    assert summary_case["session"]["status"] == "not_applicable"
    assert summary_case["session"]["event_diffs"] == []
    assert summary_case["session"]["summary_diffs"] == []
    assert summary_case["session"]["state_diffs"] == []

    state_case = next(case for case in cases if case["case_name"] == "state_update")
    assert state_case["session"]["state_diffs"] == []
    assert state_case["status"] == "matched"

    memory_case = next(case for case in cases if case["case_name"] == "memory_store_search")
    assert memory_case["memory"]["backend_expected"] == BASELINE_BACKEND_NAME
    assert memory_case["memory"]["backend_actual"] is None
    assert memory_case["memory"]["backend_actuals"] == []
    assert memory_case["memory"]["status"] == "not_applicable"
    assert memory_case["memory"]["memory_diffs"] == []

    assert report["totals"]["summary_content_mismatches"] == 0
    assert report["totals"]["summary_metadata_mismatches"] == 0

    non_memory_case = next(case for case in cases if case["case_name"] == "single_turn")
    assert non_memory_case["memory"]["status"] == "not_applicable"
    assert non_memory_case["memory"]["backend_expected"] is None
    assert non_memory_case["memory"]["backend_actual"] is None


def test_summary_report_separates_content_semantics_from_strict_metadata(monkeypatch):
    monkeypatch.delenv(SQL_URL_ENV, raising=False)
    monkeypatch.delenv(REDIS_URL_ENV, raising=False)

    report = build_replay_diff_report(ReplayBackendConfig(sql_url="sqlite:///:memory:"))
    summary_case = next(case for case in report["cases"] if case["case_name"] == "summary_generation_update")

    content_checks = summary_case["session"]["summary_content_checks"]
    metadata_checks = summary_case["session"]["summary_metadata_checks"]

    assert [check["summary_id"] for check in content_checks] == [
        "summary:session-summary-generation-update:latest",
    ]
    assert all(check["matched"] for check in content_checks)
    assert content_checks[0]["comparison"] == "normalized_text"
    assert content_checks[0]["expected_normalized_text"].startswith(
        "summary(session-summary-generation-update):"
    )
    assert "facts=3-events" in content_checks[0]["expected_normalized_text"]

    summary_metadata = next(
        check for check in metadata_checks
        if check["summary_id"] == "summary:session-summary-generation-update:latest"
    )
    metadata_by_field = {field["field"]: field for field in summary_metadata["fields"]}
    assert summary_metadata["matched"] is True
    assert metadata_by_field["session_id"]["expected"] == "session-summary-generation-update"
    assert metadata_by_field["session_id"]["matched"] is True
    assert metadata_by_field["manager_session_id"]["expected"] == "session-summary-generation-update"
    assert metadata_by_field["has_summary"]["expected"] is True
    assert metadata_by_field["has_summary_timestamp"]["expected"] is True
    assert metadata_by_field["summary_event_count"]["expected"] == 1
    assert metadata_by_field["compressed_event_count"]["expected"] == 3
    assert metadata_by_field["historical_event_count"]["expected"] == 5
    assert metadata_by_field["original_event_count"]["expected"] == 5
    assert metadata_by_field["manager_compressed_event_count"]["expected"] == 3


def test_summary_metadata_checks_do_not_hide_storage_mismatches():
    expected_snapshot = {
        "session_id": "s1",
        "events": [{
            "version": 2,
            "is_summary": True,
            "custom_metadata": {
                "summary_id": "summary-1",
                "session_id": "s1",
                "summary_version": 2,
                "supersedes": "summary-0",
                "source_event_ids": ["event-1"],
            },
            "parts": [{"text": "Summary: Alice likes green."}],
        }],
        "historical_events": [],
    }
    actual_snapshot = {
        "session_id": "s2",
        "events": [{
            "version": 3,
            "is_summary": True,
            "custom_metadata": {
                "summary_id": "summary-1",
                "session_id": "s2",
                "summary_version": 3,
                "supersedes": "summary-x",
                "source_event_ids": ["event-2"],
            },
            "parts": [{"text": "summary: alice likes green."}],
        }],
        "historical_events": [],
    }

    content_checks = build_summary_content_checks(expected_snapshot, actual_snapshot)
    metadata_checks = build_summary_metadata_checks(expected_snapshot, actual_snapshot)
    fields = {field["field"]: field for field in metadata_checks[0]["fields"]}

    assert content_checks[0]["matched"] is True
    assert metadata_checks[0]["matched"] is False
    assert fields["session_id"]["matched"] is False
    assert fields["event_version"]["matched"] is False
    assert fields["summary_version"]["matched"] is False
    assert fields["supersedes"]["matched"] is False
    assert fields["source_event_ids"]["matched"] is False


def test_replay_diff_report_fixture_matches_current_output():
    if not default_backend_matrix_enabled():
        pytest.skip("The checked-in report fixture targets the default InMemory-only matrix")

    report_path = Path(__file__).parent / "replay_consistency" / "session_memory_summary_diff_report.json"
    expected_report = json.loads(report_path.read_text(encoding="utf-8"))

    assert build_replay_diff_report() == expected_report
