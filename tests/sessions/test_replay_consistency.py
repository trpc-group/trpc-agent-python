# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end consistency tests for Session, Memory, and Summary backends."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from tests.sessions.replay_harness import build_diff_report
from tests.sessions.replay_harness import run_replay_harness
from tests.sessions.replay_harness import write_diff_report


@pytest.fixture(scope="module")
def replay_run(tmp_path_factory):
    """Run all public cases once and share the immutable result."""
    work_dir = tmp_path_factory.mktemp("session-replay")
    return asyncio.run(run_replay_harness(work_dir=work_dir))


def _case_result(run, case_id, backend):
    return next(result for result in run["results"] if result["case_id"] == case_id and result["backend"] == backend)


def _injected_run(run, case_id, mutator):
    """Build a two-backend run where one result contains a controlled defect."""
    injected = copy.deepcopy(run)
    reference_backend = run["backend_names"][0]
    reference_results = [copy.deepcopy(result) for result in run["results"] if result["backend"] == reference_backend]
    faulty_results = copy.deepcopy(reference_results)
    for result in faulty_results:
        result["backend"] = "fault_injected"
        if result["case_id"] == case_id:
            mutator(result["snapshot"])
    injected["backend_names"] = [reference_backend, "fault_injected"]
    injected["results"] = reference_results + faulty_results
    return injected


def _change_single_turn(snapshot):
    snapshot["events"][0]["author"] = "assistant"


def _change_multi_turn(snapshot):
    snapshot["events"][1], snapshot["events"][2] = snapshot["events"][2], snapshot["events"][1]


def _change_tool_call(snapshot):
    snapshot["events"][1]["content"]["parts"][0]["function_call"]["args"]["city"] = "Beijing"


def _change_state(snapshot):
    snapshot["state"]["theme"] = "light"


def _change_memory(snapshot):
    snapshot["memory"]["tea_preference"].pop()


def _lose_summary(snapshot):
    snapshot["summary"]["current"] = None


def _change_summary_version(snapshot):
    snapshot["summary"]["current"]["version"] = 99


def _change_summary_session(snapshot):
    snapshot["summary"]["current"]["session_id"] = "another-session"


def _duplicate_event(snapshot):
    snapshot["events"].append(copy.deepcopy(snapshot["events"][-1]))


def _change_summary_overwrite(snapshot):
    snapshot["summary"]["current"]["supersedes"] = "wrong-summary"


INJECTIONS = [
    ("single_turn", _change_single_turn),
    ("multi_turn", _change_multi_turn),
    ("tool_call", _change_tool_call),
    ("state_overwrite", _change_state),
    ("memory_write_read", _change_memory),
    ("summary_create_update", _lose_summary),
    ("summary_event_truncation", _change_summary_version),
    ("summary_semantic_normalization", _change_summary_session),
    ("duplicate_write_recovery", _duplicate_event),
    ("failed_summary_recovery", _change_summary_overwrite),
]


def test_all_public_cases_match_without_false_positives(replay_run, tmp_path):
    report = build_diff_report(replay_run)
    report_path = tmp_path / "session_memory_summary_diff_report.json"
    write_diff_report(report, report_path)

    assert len(replay_run["cases"]) == 10
    assert report["summary"]["passed_case_count"] == 10
    assert report["summary"]["unexpected_diff_count"] == 0
    assert report["summary"]["invariant_failure_count"] == 0
    assert report["summary"]["elapsed_seconds"] <= 30

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["report_sha256"] == report["report_sha256"]
    assert all({"events", "state", "memory", "summary"} <= set(result["snapshot"]) for case in report["cases"]
               for result in case["backend_results"].values())


@pytest.mark.parametrize(("case_id", "mutator"), INJECTIONS, ids=[case_id for case_id, _ in INJECTIONS])
def test_each_public_case_detects_an_injected_inconsistency(replay_run, case_id, mutator):
    report = build_diff_report(_injected_run(replay_run, case_id, mutator))
    case_report = next(case for case in report["cases"] if case["case_id"] == case_id)

    assert case_report["status"] == "failed"
    assert case_report["differences"]
    assert all(difference["session_id"] == case_report["session_id"] for difference in case_report["differences"])
    assert all(difference["path"] for difference in case_report["differences"])
    assert all("reference_value" in difference and "backend_value" in difference
               for difference in case_report["differences"])
    assert any(difference["event_index"] is not None or difference["summary_id"] is not None
               or difference["domain"] in {"state", "memory"} for difference in case_report["differences"])


def test_summary_loss_overwrite_and_wrong_session_are_all_detected(replay_run):
    scenarios = [
        ("summary_create_update", _lose_summary, "$.summary.current"),
        ("failed_summary_recovery", _change_summary_overwrite, "$.summary.current.supersedes"),
        ("summary_semantic_normalization", _change_summary_session, "$.summary.current.session_id"),
    ]

    for case_id, mutator, expected_path in scenarios:
        report = build_diff_report(_injected_run(replay_run, case_id, mutator))
        case_report = next(case for case in report["cases"] if case["case_id"] == case_id)
        assert any(difference["path"].startswith(expected_path) for difference in case_report["differences"])
        assert all(difference["domain"] == "summary" for difference in case_report["differences"])


def test_summary_timestamps_are_valid_and_revisions_advance(replay_run):
    for result in replay_run["results"]:
        summary = result["snapshot"]["summary"]
        if summary["current"] is None:
            continue

        assert summary["current"]["updated_at"] == "<timestamp>"
        assert [revision["updated_at_order"]
                for revision in summary["revisions"]] == list(range(1,
                                                                    len(summary["revisions"]) + 1))
        assert all(revision["updated_at"] == "<timestamp>" for revision in summary["revisions"])


def test_allowed_differences_are_explicit_and_narrow(replay_run):
    report = build_diff_report(replay_run)

    assert report["allowed_diff_rules"]
    assert all(rule["scope"] in {"order_only", "mechanism_only"} for rule in report["allowed_diff_rules"])
    assert all(difference["allowed"] for case in report["cases"] for difference in case["allowed_diffs"])
    assert report["summary"]["allowed_diff_count"] >= 1


def test_inmemory_only_lightweight_mode(tmp_path):
    run = asyncio.run(run_replay_harness(work_dir=tmp_path, backend_names=["inmemory"]))
    report = build_diff_report(run)

    assert report["mode"] == "inmemory-only"
    assert report["summary"]["backend_count"] == 1
    assert report["summary"]["passed_case_count"] == 10
    assert report["summary"]["invariant_failure_count"] == 0
    assert report["summary"]["elapsed_seconds"] <= 30
