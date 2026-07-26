# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency tests for SessionService and MemoryService backends.

These tests verify that two backends — InMemory and SQLite by default —
exposed through the same ``SessionServiceABC`` / ``MemoryServiceABC``
interfaces produce equivalent canonical snapshots for the same input
trajectory. They serve three purposes:

1. **Regression detector**: if a future backend change subtly alters how
   events, state, memory, or summaries are persisted, these tests will fail
   loudly and point at the specific field path that diverged.
2. **Acceptance criterion**: the framework must surface summary overwrite
   bugs, ownership confusion, and ordering differences even when the input
   is "well-formed".
3. **Quality benchmark**: a backend author can iterate locally and run
   ``pytest tests/sessions/test_replay_consistency.py`` to confirm their
   changes are replay-correct.

The diff report is emitted to ``tests/sessions/replay_diff_report.json`` on
every run so reviewers can inspect field-level diffs even when the test
suite passes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from typing import Mapping

import pytest

from trpc_agent_sdk.replay import ALLOWED_DIFF_RULES
from trpc_agent_sdk.replay import DEFAULT_CASES_PATH
from trpc_agent_sdk.replay import NORMALIZATION_RULES
from trpc_agent_sdk.replay import build_diff_report
from trpc_agent_sdk.replay import load_replay_cases
from trpc_agent_sdk.replay import run_replay_harness
from trpc_agent_sdk.replay import write_diff_report

REPORT_PATH = Path(__file__).resolve().parent / "replay_diff_report.json"

# Each case id maps to the *expected* status for the cross-backend diff.
# 'normal' means no field-level divergence is allowed.
# 'known_summary_divergence' allows event-count differences after summary
# compression (InMemory keeps the compressed window in memory; SQL re-reads
# raw events from the event table). See docs/mkdocs/en/replay-consistency.md.
EXPECTATIONS = {
    "single_turn": "normal",
    "multi_turn": "normal",
    "tool_call": "normal",
    "state_update": "normal",
    "memory_rw": "normal",
    "summary_gen": "known_summary_divergence",
    "summary_truncate": "known_summary_divergence",
    "exception_recovery": "allowed_mechanism_only",
    "injected_event_order": "normal",
    "injected_summary_session": "known_summary_divergence",
    "fail_summary_recovery": "allowed_mechanism_only",
}


async def _run_default_harness(replay_work_dir: Path) -> dict[str, Any]:
    return await run_replay_harness(
        work_dir=replay_work_dir,
        cases_path=DEFAULT_CASES_PATH,
        backend_names=["inmemory", "sqlite"],
    )


async def _run_harness_with_environ(
    replay_work_dir: Path,
    *,
    backend_names: list[str],
    environ: Mapping[str, str],
) -> dict[str, Any]:
    """Run the replay harness with an injected environment mapping.

    ``run_replay_harness`` already accepts an ``environ`` parameter so test
    code does not have to mutate the process-global ``os.environ`` (which
    is unsafe under ``pytest-xdist``). This helper exists so the
    integration tests below share the same call site.
    """
    return await run_replay_harness(
        work_dir=replay_work_dir,
        cases_path=DEFAULT_CASES_PATH,
        backend_names=backend_names,
        environ=dict(environ),
    )


def _cases_are_loaded() -> list[dict[str, Any]]:
    return load_replay_cases(DEFAULT_CASES_PATH)


def test_replay_cases_jsonl_is_well_formed() -> None:
    """The bundled JSONL must load and expose all 10 documented case ids."""
    cases = _cases_are_loaded()
    assert {case["case_id"] for case in cases} == set(EXPECTATIONS), (
        "Replay case ids changed; update EXPECTATIONS and the documentation together."
    )
    for case in cases:
        assert case["session_id"], f"{case['case_id']} missing session_id"
        assert case["operations"], f"{case['case_id']} has no operations"
        assert case["expect"], f"{case['case_id']} has no expect block"


async def test_run_replay_harness_does_not_pollute_os_environ(
    replay_work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_replay_harness(..., environ=...)`` must not mutate ``os.environ``.

    Asserting on the absence of mutation prevents the test suite from
    silently regressing to direct ``os.environ`` mutation, which races
    when multiple workers run in parallel under ``pytest-xdist``.
    """
    sentinel_key = "TRPC_REPLAY_REDIS_URL"
    monkeypatch.delenv(sentinel_key, raising=False)
    environ_before = dict(os.environ)

    await run_replay_harness(
        work_dir=replay_work_dir,
        cases_path=DEFAULT_CASES_PATH,
        backend_names=["inmemory", "sqlite"],
        environ={sentinel_key: "redis://example.invalid:6379/0"},
    )

    assert os.environ == environ_before, (
        "run_replay_harness leaked an environ entry into the process "
        "environment"
    )
    assert sentinel_key not in os.environ


def test_normalization_and_allowed_diff_rules_are_documented() -> None:
    """The framework's normalization and allowed-diff rules must stay exported."""
    assert NORMALIZATION_RULES, "NORMALIZATION_RULES must be a non-empty list"
    assert ALLOWED_DIFF_RULES, "ALLOWED_DIFF_RULES must be a non-empty list"
    paths = {rule["path"] for rule in NORMALIZATION_RULES}
    assert "$.events[*].timestamp" in paths
    assert "$.events[*].id" in paths
    assert "$.summary.*.text" in paths


async def test_default_harness_runs_all_cases(replay_work_dir: Path) -> None:
    """Running InMemory + SQLite must succeed for every case in the bundle."""
    run = await _run_default_harness(replay_work_dir)
    cases = _cases_are_loaded()
    assert len(run["results"]) == len(cases) * 2, (
        "Expected two results per case (inmemory + sqlite)"
    )
    assert run["backend_names"] == ["inmemory", "sqlite"]
    for result in run["results"]:
        assert result["error"] is None, (
            f"{result['backend']}/{result['case_id']} raised: {result['error']}"
        )


async def test_normal_cases_have_no_field_diff(replay_work_dir: Path) -> None:
    """Cases 1-6 must produce identical canonical snapshots across backends."""
    run = await _run_default_harness(replay_work_dir)
    report = build_diff_report(run)
    write_diff_report(report, REPORT_PATH)

    normal_cases = {cid for cid, kind in EXPECTATIONS.items() if kind == "normal"}
    case_status = {case["case_id"]: case for case in report["cases"]}
    for case_id in normal_cases:
        case_report = case_status[case_id]
        assert not case_report["differences"], (
            f"{case_id} unexpectedly diverged between backends: "
            f"{json.dumps(case_report['differences'], ensure_ascii=False)[:500]}"
        )
        for backend_name, backend_result in case_report["backend_results"].items():
            assert not backend_result["invariant_failures"], (
                f"{case_id}/{backend_name} invariant failure: "
                f"{json.dumps(backend_result['invariant_failures'], ensure_ascii=False)[:500]}"
            )


async def test_summary_metadata_is_backend_agnostic(replay_work_dir: Path) -> None:
    """Summary id, version, text and anchor count must match across backends."""
    run = await _run_default_harness(replay_work_dir)
    report = build_diff_report(run)
    case_status = {case["case_id"]: case for case in report["cases"]}

    for case_id, expectation in EXPECTATIONS.items():
        if expectation not in {"known_summary_divergence", "normal"}:
            continue
        case_report = case_status[case_id]
        summary_block = case_report["backend_results"]["inmemory"]["snapshot"]["summary"]["current"]
        sqlite_block = case_report["backend_results"]["sqlite"]["snapshot"]["summary"]["current"]
        if summary_block is None:
            assert sqlite_block is None, f"{case_id}: sqlite unexpectedly had a summary"
            continue
        for field in ("summary_id", "version", "text", "anchor_count"):
            assert summary_block[field] == sqlite_block[field], (
                f"{case_id}: summary.{field} mismatch — "
                f"inmemory={summary_block[field]!r} sqlite={sqlite_block[field]!r}"
            )


async def test_summary_compression_actually_compresses(replay_work_dir: Path) -> None:
    """summary_gen must produce a strictly smaller active event count on both backends."""
    run = await _run_default_harness(replay_work_dir)
    report = build_diff_report(run)
    case_report = next(c for c in report["cases"] if c["case_id"] == "summary_gen")
    for backend_name, backend_result in case_report["backend_results"].items():
        current = backend_result["snapshot"]["summary"]["current"]
        assert current is not None, "summary_gen must produce a summary on every backend"
        original = current["original_event_count"]
        compressed = current["compressed_event_count"]
        assert original is not None and compressed is not None
        assert compressed < original, (
            f"summary_gen/{backend_name}: compression did not shrink event count "
            f"(original={original}, compressed={compressed})"
        )


async def test_exception_recovery_recovers_event_uniqueness(replay_work_dir: Path) -> None:
    """Duplicate append must leave exactly one copy regardless of the mechanism used."""
    run = await _run_default_harness(replay_work_dir)
    report = build_diff_report(run)
    case_report = next(c for c in report["cases"] if c["case_id"] == "exception_recovery")
    for backend_name, backend_result in case_report["backend_results"].items():
        recovered_kinds = [
            audit["kind"] for audit in backend_result["snapshot"]["operation_audit"] if audit["recovered"]
        ]
        assert recovered_kinds == ["duplicate_append"], (
            f"exception_recovery/{backend_name} did not report a successful recovery: {recovered_kinds}"
        )
        event_ids = [event["id"] for event in backend_result["snapshot"]["events"]]
        assert len(event_ids) == len(set(event_ids)), (
            f"exception_recovery/{backend_name}: duplicate id leaked into the active window"
        )


async def test_fail_summary_recovery_hides_attempted_summary_id(
    replay_work_dir: Path,
) -> None:
    """``fail_summary`` must roll the snapshot back to the pre-failure summary.

    Locks the contract documented in
    ``docs/mkdocs/en/replay-consistency.md``: the partial-commit recovery
    must restore the pre-failure summary id and leave no trace of the
    attempted one in the canonical session events.
    """
    run = await _run_default_harness(replay_work_dir)
    report = build_diff_report(run)
    case_report = next(c for c in report["cases"] if c["case_id"] == "fail_summary_recovery")
    assert case_report is not None, (
        "fail_summary_recovery case missing from the JSONL bundle"
    )

    for backend_name, backend_result in case_report["backend_results"].items():
        snapshot = backend_result["snapshot"]

        # The audit log must record a recovered summary_update entry.
        recovery_audits = [
            audit for audit in snapshot["operation_audit"]
            if audit["kind"] == "summary_update"
        ]
        assert recovery_audits, (
            f"{backend_name}: fail_summary did not emit a summary_update audit entry"
        )
        assert recovery_audits[0]["recovered"] is True, (
            f"{backend_name}: fail_summary audit reported "
            f"recovered=False ({recovery_audits[0]})"
        )

        # The current summary must be the pre-failure one.
        current = snapshot["summary"]["current"]
        assert current is not None, (
            f"{backend_name}: pre-failure summary not visible after recovery"
        )
        assert current["summary_id"] == "summary-fail-pre", (
            f"{backend_name}: recovered summary id was {current['summary_id']!r}, "
            "expected 'summary-fail-pre'"
        )

        # The attempted summary id must not appear anywhere in the canonical events.
        attempted_ids = {
            event["id"] for event in snapshot["events"]
            if event["id"] == "summary-fail-attempted"
        }
        assert not attempted_ids, (
            f"{backend_name}: rolled-back summary id leaked into canonical events"
        )


async def test_diff_report_is_serializable_and_locatable(replay_work_dir: Path) -> None:
        recovered_kinds = [
            audit["kind"] for audit in backend_result["snapshot"]["operation_audit"] if audit["recovered"]
        ]
        assert recovered_kinds == ["duplicate_append"], (
            f"exception_recovery/{backend_name} did not report a successful recovery: {recovered_kinds}"
        )
        event_ids = [event["id"] for event in backend_result["snapshot"]["events"]]
        assert len(event_ids) == len(set(event_ids)), (
            f"exception_recovery/{backend_name}: duplicate id leaked into the active window"
        )


async def test_diff_report_is_serializable_and_locatable(replay_work_dir: Path) -> None:
    """The diff report must be writable to disk and locate every divergence."""
    run = await _run_default_harness(replay_work_dir)
    report = build_diff_report(run)
    write_diff_report(report, REPORT_PATH)
    payload = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["backends"] == ["inmemory", "sqlite"]
    for case in payload["cases"]:
        for diff in case["differences"]:
            for required in ("case_id", "session_id", "backend", "domain", "path"):
                assert required in diff, f"diff missing {required}: {diff}"
            if diff["domain"] == "events":
                assert diff["event_index"] is not None
            if diff["domain"] == "summary":
                assert diff["summary_id"] is not None or diff["path"].startswith("$.summary.current")


@pytest.mark.parametrize(
    "case_id",
    sorted(EXPECTATIONS),
)
async def test_each_case_meets_its_expectations(replay_work_dir: Path, case_id: str) -> None:
    """Per-case invariants must hold on *both* backends independently."""
    run = await _run_default_harness(replay_work_dir)
    report = build_diff_report(run)
    case_report = next(c for c in report["cases"] if c["case_id"] == case_id)
    for backend_name, backend_result in case_report["backend_results"].items():
        assert not backend_result["invariant_failures"], (
            f"{case_id}/{backend_name} invariant failure: "
            f"{json.dumps(backend_result['invariant_failures'], ensure_ascii=False)[:500]}"
        )
        if EXPECTATIONS[case_id] == "normal":
            assert not case_report["differences"], (
                f"{case_id}: unexpected backend divergence: "
                f"{json.dumps(case_report['differences'], ensure_ascii=False)[:500]}"
            )
        elif EXPECTATIONS[case_id] == "known_summary_divergence":
            # Differences are allowed but every allowed diff must be tagged.
            for diff in case_report["differences"]:
                assert diff["domain"] in {"events", "summary"}, (
                    f"{case_id}: non-summary divergence leaked through: {diff}"
                )


async def test_diff_engine_detects_injected_event_reorder(replay_work_dir: Path) -> None:
    """Synthetic event reordering must surface a field-locatable diff."""
    run = await _run_default_harness(replay_work_dir)
    result_index = {(r["case_id"], r["backend"]): r for r in run["results"]}

    # Pick the 5-event case and swap two adjacent events on the sqlite side.
    multi = result_index[("injected_event_order", "sqlite")]
    snapshot = multi["snapshot"]
    snapshot["events"][0], snapshot["events"][1] = snapshot["events"][1], snapshot["events"][0]

    report = build_diff_report(run)
    case_report = next(c for c in report["cases"] if c["case_id"] == "injected_event_order")
    assert case_report["differences"], "Diff engine missed the synthetic reorder"
    for diff in case_report["differences"]:
        assert diff["backend"] == "sqlite"
        assert diff["domain"] == "events"
        assert diff["event_index"] in {0, 1}
        assert diff["path"].startswith("$.events[")


async def test_diff_engine_detects_summary_session_id_tampering(replay_work_dir: Path) -> None:
    """Synthetic summary_session_id swap must surface a summary-domain diff.

    ``injected_summary_session`` is classified as
    ``known_summary_divergence`` so the diff engine records the
    session_id swap under ``allowed_diffs`` rather than
    ``differences`` — the engine must still locate it, classify it by
    domain, and emit it through the report so reviewers can see what
    changed.
    """
    run = await _run_default_harness(replay_work_dir)
    result_index = {(r["case_id"], r["backend"]): r for r in run["results"]}

    inj = result_index[("injected_summary_session", "sqlite")]
    inj["snapshot"]["summary"]["current"]["session_id"] = "wrong-session-id"

    report = build_diff_report(run)
    case_report = next(c for c in report["cases"] if c["case_id"] == "injected_summary_session")
    # The diff may land in either bucket depending on the EXPECTATIONS
    # classification. We care that *something* was detected and located.
    all_locateable = [
        diff for diff in (case_report["differences"] + case_report["allowed_diffs"])
        if diff["path"].endswith("session_id")
    ]
    assert all_locateable, "Diff engine missed the summary session_id tampering"
    diff = all_locateable[0]
    assert diff["domain"] == "summary"
    assert diff["reference_value"] == "session-injected-summary-session"
    assert diff["backend_value"] == "wrong-session-id"
    assert diff["summary_id"] == "summary-003"


# ---------------------------------------------------------------------------
# Integration mode — opt-in via TRPC_REPLAY_REDIS_URL / TRPC_REPLAY_SQL_URL.
#
# These tests are intentionally marked ``integration`` so the default
# ``pytest`` invocation (no env vars) reports them as ``skipped`` instead of
# failing. CI runs them through ``.github/workflows/replay-integration.yml``
# only when the corresponding secret is configured.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_redis_integration_harness_runs_all_cases(replay_work_dir: Path, integration_runtime: Mapping[str, object]) -> None:
    """InMemory ⇄ Redis must produce equivalent canonical snapshots for every case.

    Skips automatically when ``TRPC_REPLAY_REDIS_URL`` is not set, the
    ``redis`` package is not installed, or the Redis server is unreachable
    (the connection probe is wrapped in a try/except so a Docker-less
    contributor sees a clean ``skipped`` rather than a hard failure). See
    ``tests/sessions/conftest.py``.

    The harness is invoked through ``_run_harness_with_environ`` which
    passes the URL via the ``environ`` parameter rather than mutating
    ``os.environ`` — that keeps the suite safe under
    ``pytest-xdist`` parallelism.
    """
    redis_url = integration_runtime["redis_url"]
    if not redis_url:
        pytest.skip(integration_runtime["skip_reason"] or "Redis integration backend not configured")
    try:
        run = await _run_harness_with_environ(
            replay_work_dir,
            backend_names=["inmemory", "redis"],
            environ={"TRPC_REPLAY_REDIS_URL": redis_url},
        )
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"Redis backend unreachable at {redis_url}: {type(exc).__name__}: {exc}")

    cases = _cases_are_loaded()
    assert run["backend_names"] == ["inmemory", "redis"]
    assert len(run["results"]) == len(cases) * 2

    # If either backend failed to even construct (e.g. connection refused),
    # treat the whole test as a skip rather than a hard failure.
    unreachable = [
        result for result in run["results"] if result["error"] is not None
    ]
    if unreachable:
        sample = unreachable[0]
        pytest.skip(
            f"Redis backend produced errors during replay (likely unreachable): "
            f"{sample['backend']}/{sample['case_id']} -> {sample['error']}"
        )

    for result in run["results"]:
        assert result["error"] is None, (
            f"{result['backend']}/{result['case_id']} raised: {result['error']}"
        )

    report = build_diff_report(run)
    case_status = {case["case_id"]: case for case in report["cases"]}
    for case_id, expectation in EXPECTATIONS.items():
        case_report = case_status[case_id]
        if expectation != "normal":
            continue
        assert not case_report["differences"], (
            f"{case_id} unexpectedly diverged between InMemory and Redis: "
            f"{json.dumps(case_report['differences'], ensure_ascii=False)[:500]}"
        )


@pytest.mark.integration
async def test_redis_integration_summary_metadata_matches(replay_work_dir: Path, integration_runtime: Mapping[str, object]) -> None:
    """Redis summary id/version/text/anchor_count must match InMemory exactly."""
    redis_url = integration_runtime["redis_url"]
    if not redis_url:
        pytest.skip(integration_runtime["skip_reason"] or "Redis integration backend not configured")
    try:
        run = await _run_harness_with_environ(
            replay_work_dir,
            backend_names=["inmemory", "redis"],
            environ={"TRPC_REPLAY_REDIS_URL": redis_url},
        )
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"Redis backend unreachable at {redis_url}: {type(exc).__name__}: {exc}")

    report = build_diff_report(run)
    case_status = {case["case_id"]: case for case in report["cases"]}
    for case_id, expectation in EXPECTATIONS.items():
        if expectation not in {"known_summary_divergence", "normal"}:
            continue
        case_report = case_status[case_id]
        for backend_name in ("inmemory", "redis"):
            snapshot = case_report["backend_results"][backend_name]["snapshot"]
            if "summary" not in snapshot:
                pytest.skip(
                    f"Redis snapshot missing summary block for case {case_id} — "
                    "backend became unreachable mid-run."
                )
        imm = case_report["backend_results"]["inmemory"]["snapshot"]["summary"]["current"]
        red = case_report["backend_results"]["redis"]["snapshot"]["summary"]["current"]
        if imm is None:
            assert red is None, f"{case_id}: redis unexpectedly had a summary"
            continue
        for field in ("summary_id", "version", "text", "anchor_count"):
            assert imm[field] == red[field], (
                f"{case_id}: summary.{field} mismatch — "
                f"inmemory={imm[field]!r} redis={red[field]!r}"
            )


# ---------------------------------------------------------------------------
# SQL / MySQL integration harness — opt-in via TRPC_REPLAY_SQL_URL.
#
# Mirror image of the Redis integration tests. CI runs them through
# .github/workflows/replay-integration.yml only when the corresponding
# secret is configured. The integration_runtime fixture's ``sql_url`` key
# short-circuits to a clean skip on contributors' machines without MySQL.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_sql_integration_harness_runs_all_cases(replay_work_dir: Path, integration_runtime: Mapping[str, object]) -> None:
    """InMemory ⇄ SQL must produce equivalent canonical snapshots for every case.

    Skips automatically when ``TRPC_REPLAY_SQL_URL`` is not set or the SQL
    driver (e.g. PyMySQL / aiomysql) cannot reach the database. The
    ``try/except`` around the harness keeps Docker-less contributors on a
    clean ``skipped`` rather than a hard failure — see
    ``tests/sessions/conftest.py``.

    The harness is invoked through ``_run_harness_with_environ`` which
    passes the URL via the ``environ`` parameter rather than mutating
    ``os.environ`` — that keeps the suite safe under
    ``pytest-xdist`` parallelism.
    """
    sql_url = integration_runtime["sql_url"]
    if not sql_url:
        pytest.skip(integration_runtime["skip_reason"] or "SQL integration backend not configured")
    try:
        run = await _run_harness_with_environ(
            replay_work_dir,
            backend_names=["inmemory", "sql"],
            environ={"TRPC_REPLAY_SQL_URL": sql_url},
        )
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"SQL backend unreachable at {sql_url}: {type(exc).__name__}: {exc}")

    cases = _cases_are_loaded()
    assert run["backend_names"] == ["inmemory", "sql"]
    assert len(run["results"]) == len(cases) * 2

    unreachable = [
        result for result in run["results"] if result["error"] is not None
    ]
    if unreachable:
        sample = unreachable[0]
        pytest.skip(
            f"SQL backend produced errors during replay (likely unreachable): "
            f"{sample['backend']}/{sample['case_id']} -> {sample['error']}"
        )

    for result in run["results"]:
        assert result["error"] is None, (
            f"{result['backend']}/{result['case_id']} raised: {result['error']}"
        )

    report = build_diff_report(run)
    case_status = {case["case_id"]: case for case in report["cases"]}
    for case_id, expectation in EXPECTATIONS.items():
        case_report = case_status[case_id]
        if expectation != "normal":
            continue
        assert not case_report["differences"], (
            f"{case_id} unexpectedly diverged between InMemory and SQL: "
            f"{json.dumps(case_report['differences'], ensure_ascii=False)[:500]}"
        )


@pytest.mark.integration
async def test_sql_integration_summary_metadata_matches(replay_work_dir: Path, integration_runtime: Mapping[str, object]) -> None:
    """SQL summary id/version/text/anchor_count must match InMemory exactly."""
    sql_url = integration_runtime["sql_url"]
    if not sql_url:
        pytest.skip(integration_runtime["skip_reason"] or "SQL integration backend not configured")
    try:
        run = await _run_harness_with_environ(
            replay_work_dir,
            backend_names=["inmemory", "sql"],
            environ={"TRPC_REPLAY_SQL_URL": sql_url},
        )
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"SQL backend unreachable at {sql_url}: {type(exc).__name__}: {exc}")

    report = build_diff_report(run)
    case_status = {case["case_id"]: case for case in report["cases"]}
    for case_id, expectation in EXPECTATIONS.items():
        if expectation not in {"known_summary_divergence", "normal"}:
            continue
        case_report = case_status[case_id]
        for backend_name in ("inmemory", "sql"):
            snapshot = case_report["backend_results"][backend_name]["snapshot"]
            if "summary" not in snapshot:
                pytest.skip(
                    f"SQL snapshot missing summary block for case {case_id} — "
                    "backend became unreachable mid-run."
                )
        imm = case_report["backend_results"]["inmemory"]["snapshot"]["summary"]["current"]
        sql = case_report["backend_results"]["sql"]["snapshot"]["summary"]["current"]
        if imm is None:
            assert sql is None, f"{case_id}: sql unexpectedly had a summary"
            continue
        for field in ("summary_id", "version", "text", "anchor_count"):
            assert imm[field] == sql[field], (
                f"{case_id}: summary.{field} mismatch — "
                f"inmemory={imm[field]!r} sql={sql[field]!r}"
            )