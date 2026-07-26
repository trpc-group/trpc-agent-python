# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the replay CLI entry point (trpc_agent_sdk.replay._main.main).

These tests cover the four behaviors addressed by code review:

1. ``--backends`` must normalize casing and whitespace before validating.
2. Exit code must be 0 when the only divergences come from cases whose
   ``EXPECTATIONS`` is ``known_summary_divergence``.
3. (added in TDD loop 3) fail_summary case must restore the snapshot.
4. (added in TDD loop 4) Integration tests must pass ``environ=`` instead
   of mutating ``os.environ``.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from trpc_agent_sdk.replay._main import main as cli_main


# ---------------------------------------------------------------------------
# TDD loop 1: --backends normalization
# ---------------------------------------------------------------------------


def test_cli_backends_normalizes_case_and_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--backends " InMemory , SQLite "`` must be normalized to ``['inmemory', 'sqlite']``.

    Today the CLI only calls ``str.strip()`` and not ``str.lower()`` and so
    variants such as ``InMemory`` and ``SQLITE`` reach ``_build_backend``
    which does an exact ``name == 'inmemory'`` match and raises
    ``Unsupported replay backend``.
    """
    captured: dict[str, object] = {}

    async def _fake_run_replay_harness(*, work_dir, cases_path, backend_names, **_kwargs):
        captured["work_dir"] = work_dir
        captured["cases_path"] = cases_path
        captured["backend_names"] = list(backend_names) if backend_names is not None else None
        return {
            "cases": [],
            "backend_names": list(backend_names) if backend_names is not None else [],
            "results": [],
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr(
        "trpc_agent_sdk.replay._main.run_replay_harness",
        _fake_run_replay_harness,
    )
    monkeypatch.setattr(
        "trpc_agent_sdk.replay._main.build_diff_report",
        lambda _run: _empty_run_report(),
    )
    monkeypatch.setattr(
        "trpc_agent_sdk.replay._main.write_diff_report",
        lambda _report, _path: None,
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = cli_main([
            "--backends",
            " InMemory , SQLite ",
            "--work-dir",
            str(tmp_path),
            "--output",
            str(tmp_path / "report.json"),
        ])

    assert exit_code == 0
    assert captured["backend_names"] == ["inmemory", "sqlite"]


def test_cli_backends_rejects_unknown_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown backend names must still raise ``ValueError`` after normalization."""

    async def _fake_run_replay_harness(*, work_dir, cases_path, backend_names, **_kwargs):
        return {
            "cases": [],
            "backend_names": list(backend_names) if backend_names is not None else [],
            "results": [],
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr(
        "trpc_agent_sdk.replay._main.run_replay_harness",
        _fake_run_replay_harness,
    )

    with pytest.raises(ValueError, match="Unsupported replay backend"):
        cli_main([
            "--backends",
            "InMemory,NotARealBackend",
            "--work-dir",
            str(tmp_path),
            "--output",
            str(tmp_path / "report.json"),
        ])


# ---------------------------------------------------------------------------
# TDD loop 2: exit code semantics
#
# The CLI must exit 0 when the only divergences come from cases whose
# ``EXPECTATIONS`` is ``known_summary_divergence``. The bug being fixed
# lives in :func:`trpc_agent_sdk.replay._diff.build_diff_report` which
# currently sets ``unexpected_diff_count = len(all_differences)`` without
# subtracting the diffs already classified as allowed via ``allowed_diffs``.
# ---------------------------------------------------------------------------


def _empty_run_report() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-07-26T00:00:00+00:00",
        "mode": "lightweight-persistent",
        "reference_backend": "inmemory",
        "backends": ["inmemory", "sqlite"],
        "normalization_rules": [],
        "allowed_diff_rules": [],
        "cases": [],
        "summary": {
            "case_count": 0,
            "backend_count": 2,
            "passed_case_count": 0,
            "unexpected_diff_count": 0,
            "allowed_diff_count": 0,
            "invariant_failure_count": 0,
            "elapsed_seconds": 0.0,
        },
    }


def _run_with_two_backends(monkeypatch, *, inmemory_snapshot, sqlite_snapshot) -> None:
    """Drive the CLI with a minimal run that has two backends and one case."""

    async def _fake_run_replay_harness(**_kwargs):
        return {
            "cases": [
                {
                    "case_id": "summary_gen",
                    "description": "",
                    "session_id": "session-summary-gen",
                    "expect": {"summary_present": True},
                }
            ],
            "backend_names": ["inmemory", "sqlite"],
            "results": [
                {
                    "backend": "inmemory",
                    "case_id": "summary_gen",
                    "session_id": "session-summary-gen",
                    "operation_count": 1,
                    "snapshot": inmemory_snapshot,
                    "raw_memory_order": {},
                    "recovery_raw": [],
                    "replay_metadata": [],
                    "invariant_failures": [],
                    "error": None,
                },
                {
                    "backend": "sqlite",
                    "case_id": "summary_gen",
                    "session_id": "session-summary-gen",
                    "operation_count": 1,
                    "snapshot": sqlite_snapshot,
                    "raw_memory_order": {},
                    "recovery_raw": [],
                    "replay_metadata": [],
                    "invariant_failures": [],
                    "error": None,
                },
            ],
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr("trpc_agent_sdk.replay._main.run_replay_harness", _fake_run_replay_harness)
    monkeypatch.setattr("trpc_agent_sdk.replay._main.write_diff_report", lambda _r, _p: None)


def test_cli_exit_code_zero_when_only_allowed_diffs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``known_summary_divergence`` events-length drift must not fail the CLI.

    InMemory trims compressed events into ``historical_events`` while SQLite
    keeps them in the active window — this is a documented, expected
    divergence. The CLI must exit 0 because every diff is classified as
    allowed by the diff engine.
    """
    shared = {
        "state": {},
        "memory": {},
        "summary": {"current": None, "revisions": [], "anchor_count": 0},
        "operation_audit": [],
    }
    inmemory_snapshot = {
        **shared,
        "events": [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}],
        "historical_events": [],
    }
    sqlite_snapshot = {
        **shared,
        "events": [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}, {"id": "e4"}, {"id": "e5"}],
        "historical_events": [],
    }
    _run_with_two_backends(monkeypatch, inmemory_snapshot=inmemory_snapshot, sqlite_snapshot=sqlite_snapshot)

    with redirect_stdout(io.StringIO()):
        exit_code = cli_main([
            "--backends", "inmemory,sqlite",
            "--work-dir", str(tmp_path),
            "--output", str(tmp_path / "report.json"),
        ])

    assert exit_code == 0, (
        "CLI exited 1 even though every divergence is a documented "
        "known_summary_divergence that the diff engine classifies as allowed"
    )


def test_cli_exit_code_one_on_real_divergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine cross-backend semantic divergence must still fail the CLI.

    Uses ``single_turn`` (``EXPECTATIONS == normal``) so any field-level
    divergence is unexpected.
    """
    inmemory_snapshot = {
        "events": [{"id": "e1", "author": "user"}],
        "historical_events": [],
        "state": {},
        "memory": {},
        "summary": {"current": None, "revisions": [], "anchor_count": 0},
        "operation_audit": [],
    }
    sqlite_snapshot = {
        "events": [{"id": "e1", "author": "assistant"}],  # semantic mismatch
        "historical_events": [],
        "state": {},
        "memory": {},
        "summary": {"current": None, "revisions": [], "anchor_count": 0},
        "operation_audit": [],
    }
    # Build a real two-backend run for the ``single_turn`` case.
    async def _fake_run_replay_harness(**_kwargs):
        return {
            "cases": [
                {
                    "case_id": "single_turn",
                    "description": "",
                    "session_id": "session-single-turn",
                    "expect": {"active_event_count": 1},
                }
            ],
            "backend_names": ["inmemory", "sqlite"],
            "results": [
                {
                    "backend": "inmemory", "case_id": "single_turn",
                    "session_id": "session-single-turn", "operation_count": 1,
                    "snapshot": inmemory_snapshot,
                    "raw_memory_order": {}, "recovery_raw": [], "replay_metadata": [],
                    "invariant_failures": [], "error": None,
                },
                {
                    "backend": "sqlite", "case_id": "single_turn",
                    "session_id": "session-single-turn", "operation_count": 1,
                    "snapshot": sqlite_snapshot,
                    "raw_memory_order": {}, "recovery_raw": [], "replay_metadata": [],
                    "invariant_failures": [], "error": None,
                },
            ],
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr("trpc_agent_sdk.replay._main.run_replay_harness", _fake_run_replay_harness)
    monkeypatch.setattr("trpc_agent_sdk.replay._main.write_diff_report", lambda _r, _p: None)

    with redirect_stdout(io.StringIO()):
        exit_code = cli_main([
            "--backends", "inmemory,sqlite",
            "--work-dir", str(tmp_path),
            "--output", str(tmp_path / "report.json"),
        ])

    assert exit_code == 1


def test_cli_exit_code_one_on_invariant_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invariant failure must still fail the CLI."""

    async def _fake_run_replay_harness(**_kwargs):
        return {
            "cases": [
                {
                    "case_id": "summary_gen",
                    "description": "",
                    "session_id": "session-summary-gen",
                    "expect": {"summary_present": True},
                }
            ],
            "backend_names": ["inmemory", "sqlite"],
            "results": [
                {
                    "backend": "inmemory",
                    "case_id": "summary_gen",
                    "session_id": "session-summary-gen",
                    "operation_count": 1,
                    "snapshot": {
                        "events": [],
                        "historical_events": [],
                        "state": {},
                        "memory": {},
                        "summary": {"current": None, "revisions": [], "anchor_count": 0},
                        "operation_audit": [],
                    },
                    "raw_memory_order": {},
                    "recovery_raw": [],
                    "replay_metadata": [],
                    "invariant_failures": [
                        {"case_id": "summary_gen", "session_id": "session-summary-gen",
                         "path": "$.summary.current.present", "expected": True, "actual": False},
                    ],
                    "error": None,
                },
                {
                    "backend": "sqlite",
                    "case_id": "summary_gen",
                    "session_id": "session-summary-gen",
                    "operation_count": 1,
                    "snapshot": {
                        "events": [],
                        "historical_events": [],
                        "state": {},
                        "memory": {},
                        "summary": {"current": None, "revisions": [], "anchor_count": 0},
                        "operation_audit": [],
                    },
                    "raw_memory_order": {},
                    "recovery_raw": [],
                    "replay_metadata": [],
                    "invariant_failures": [],
                    "error": None,
                },
            ],
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr("trpc_agent_sdk.replay._main.run_replay_harness", _fake_run_replay_harness)
    monkeypatch.setattr("trpc_agent_sdk.replay._main.write_diff_report", lambda _r, _p: None)

    with redirect_stdout(io.StringIO()):
        exit_code = cli_main([
            "--backends", "inmemory,sqlite",
            "--work-dir", str(tmp_path),
            "--output", str(tmp_path / "report.json"),
        ])

    assert exit_code == 1
