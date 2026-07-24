# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Lightweight replay consistency suite for Issue #89."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from .adapters import InMemoryReplayAdapter
from .adapters import FaultInjectingAdapter
from .adapters import ReplayInjectedFault
from .adapters import SQLiteReplayAdapter
from .canonicalize import canonicalize_snapshot
from .capabilities import capabilities_for
from .cases import ReplayCase
from .cases import standard_cases
from .diff import AllowedDiffRule
from .diff import compare_snapshots
from .mutations import MutationOperator
from .mutations import registered_mutations
from .oracle import validate_snapshot_contract
from .reporting import ReplayReport
from .reporting import build_metrics
from .reporting import write_json_report
from .reporting import write_markdown_report


def _run(coro):
    return asyncio.run(coro)


async def _run_case(case: ReplayCase, tmp_path: Path):
    left = InMemoryReplayAdapter(case=case)
    right = SQLiteReplayAdapter(case=case, workdir=tmp_path)
    await left.setup()
    await right.setup()
    try:
        left_snapshot = await left.replay()
        right_snapshot = await right.replay()
        return left_snapshot, right_snapshot
    finally:
        await left.close()
        await right.close()


def _case_by_id(case_id: str) -> ReplayCase:
    return next(case for case in standard_cases() if case.case_id == case_id)


@pytest.mark.replay_lightweight
def test_replay_cases_manifest_matches_fixtures():
    manifest_path = Path(__file__).with_name("replay_cases_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = {case.case_id: case for case in standard_cases()}
    assert [item["case_id"] for item in manifest] == [case.case_id for case in standard_cases()]
    for item in manifest:
        case = cases[item["case_id"]]
        assert item["description"] == case.description
        assert item["operations"] == [op.kind for op in case.operations]


@pytest.mark.replay_lightweight
def test_cases_are_well_formed():
    cases = standard_cases()
    assert len(cases) >= 10
    case_ids = [case.case_id for case in cases]
    assert len(case_ids) == len(set(case_ids))
    for case in cases:
        assert case.operations
        assert all(op.kind for op in case.operations)


@pytest.mark.replay_lightweight
def test_snapshot_contains_all_entities(tmp_path):
    case = _case_by_id("all_entities_contract")
    left_snapshot, _ = _run(_run_case(case, tmp_path))
    data = left_snapshot.to_dict()
    assert data["sessions"]
    assert data["sessions"][0]["events"]
    assert data["sessions"][0]["state"]
    assert data["memory"]
    assert data["summaries"]


@pytest.mark.replay_lightweight
def test_oracle_accepts_standard_snapshots(tmp_path):
    for case in standard_cases():
        left_snapshot, right_snapshot = _run(_run_case(case, tmp_path))
        assert validate_snapshot_contract(canonicalize_snapshot(left_snapshot)) == []
        assert validate_snapshot_contract(canonicalize_snapshot(right_snapshot)) == []


@pytest.mark.replay_lightweight
def test_lightweight_replay_cases(tmp_path):
    start = time.perf_counter()
    all_diffs = []
    passed = 0
    cases = standard_cases()
    try:
        for case in cases:
            left_snapshot, right_snapshot = _run(_run_case(case, tmp_path))
            left = canonicalize_snapshot(left_snapshot)
            right = canonicalize_snapshot(right_snapshot)
            diffs = compare_snapshots(
                left,
                right,
                case_id=case.case_id,
                backend_pair=("in_memory", "sqlite"),
            )
            all_diffs.extend(diffs)
            if not [diff for diff in diffs if not diff.allowed]:
                passed += 1
        duration = time.perf_counter() - start
        metrics = build_metrics(
            normal_case_count=len(cases),
            normal_case_pass_count=passed,
            diffs=all_diffs,
            lightweight_duration_seconds=duration,
        )
        report = ReplayReport(
            case_id="lightweight",
            backend_pair=("in_memory", "sqlite"),
            metrics=metrics,
            diffs=[diff.to_dict() for diff in all_diffs],
            capabilities=capabilities_for("in_memory", "sqlite"),
        )
        _write_report_pair(tmp_path, "session_memory_summary_diff_report", report)
        unexpected = [diff for diff in all_diffs if not diff.allowed]
        assert unexpected == []
        assert metrics.false_positive_rate <= 0.05
        assert metrics.lightweight_duration_seconds <= 30.0
    except Exception:
        metrics = build_metrics(
            normal_case_count=len(cases),
            normal_case_pass_count=passed,
            diffs=all_diffs,
            lightweight_duration_seconds=time.perf_counter() - start,
        )
        report = ReplayReport(
            case_id="lightweight",
            backend_pair=("in_memory", "sqlite"),
            metrics=metrics,
            diffs=[diff.to_dict() for diff in all_diffs],
            capabilities=capabilities_for("in_memory", "sqlite"),
        )
        _write_report_pair(tmp_path, "session_memory_summary_diff_report", report)
        raise


@pytest.mark.replay_lightweight
def test_mutation_detection(tmp_path):
    case = _case_by_id("all_entities_contract")
    left_snapshot, _ = _run(_run_case(case, tmp_path))
    reference = canonicalize_snapshot(left_snapshot)
    detected = 0
    all_diffs = []
    mutations = registered_mutations()
    for mutation in mutations:
        mutated = mutation.mutate(reference)
        diffs = compare_snapshots(
            reference,
            mutated,
            case_id=f"{case.case_id}:{mutation.name}",
            backend_pair=("reference", "mutated"),
        )
        all_diffs.extend(diffs)
        categories = {diff.category for diff in diffs if not diff.allowed}
        if mutation.expected_category in categories:
            detected += 1
        else:
            raise AssertionError(f"{mutation.name} did not produce {mutation.expected_category}: {categories}")
    metrics = build_metrics(
        normal_case_count=1,
        normal_case_pass_count=1,
        diffs=all_diffs,
        mutation_total=len(mutations),
        mutation_detected=detected,
    )
    report = ReplayReport(
        case_id="mutation_detection",
        backend_pair=("reference", "mutated"),
        metrics=metrics,
        diffs=[diff.to_dict() for diff in all_diffs],
    )
    _write_json_report(tmp_path, "session_memory_summary_mutation_report", report)
    assert metrics.mutation_detection_rate == 1.0


@pytest.mark.replay_lightweight
def test_required_public_cases_detect_injected_inconsistency(tmp_path):
    matrix = json.loads(Path(__file__).with_name("acceptance_matrix.json").read_text(encoding="utf-8"))
    required_case_ids = matrix["required_cases"]
    detected = 0
    survived = []
    all_diffs = []

    for case_id in required_case_ids:
        case = _case_by_id(case_id)
        left_snapshot, _ = _run(_run_case(case, tmp_path))
        reference = canonicalize_snapshot(left_snapshot)
        mutated = _delete_first_observable_event(reference)
        diffs = compare_snapshots(
            reference,
            mutated,
            case_id=f"{case.case_id}:delete_first_observable_event",
            backend_pair=("reference", "mutated"),
        )
        all_diffs.extend(diffs)
        categories = {diff.category for diff in diffs if not diff.allowed}
        if "missing_entity" in categories:
            detected += 1
        else:
            survived.append(case.case_id)

    metrics = build_metrics(
        normal_case_count=len(required_case_ids),
        normal_case_pass_count=len(required_case_ids),
        diffs=all_diffs,
        mutation_total=len(required_case_ids),
        mutation_detected=detected,
        survived_mutations=survived,
    )
    report = ReplayReport(
        case_id="required_case_mutation_matrix",
        backend_pair=("reference", "mutated"),
        metrics=metrics,
        diffs=[diff.to_dict() for diff in all_diffs],
    )
    _write_json_report(tmp_path, "session_memory_summary_required_case_mutation_report", report)
    assert survived == []
    assert metrics.mutation_detection_rate == 1.0


@pytest.mark.replay_lightweight
def test_summary_defect_detection(tmp_path):
    case = _case_by_id("summary_defect_specials")
    left_snapshot, _ = _run(_run_case(case, tmp_path))
    reference = canonicalize_snapshot(left_snapshot)
    required = {
        "loss": "summary_missing",
        "overwrite": "summary_version_mismatch",
        "owner": "summary_owner_mismatch",
    }
    detections = {"loss": 0, "overwrite": 0, "owner": 0}
    all_diffs = []
    for mutation in _summary_mutations():
        mutated = mutation.mutate(reference)
        diffs = compare_snapshots(
            reference,
            mutated,
            case_id=f"{case.case_id}:{mutation.name}",
            backend_pair=("reference", "mutated"),
        )
        all_diffs.extend(diffs)
        categories = {diff.category for diff in diffs if not diff.allowed}
        assert mutation.summary_defect is not None
        assert required[mutation.summary_defect] in categories
        detections[mutation.summary_defect] += 1
    metrics = build_metrics(
        normal_case_count=1,
        normal_case_pass_count=1,
        diffs=all_diffs,
        mutation_total=len(_summary_mutations()),
        mutation_detected=len(_summary_mutations()),
        summary_loss_detected=detections["loss"],
        summary_overwrite_detected=detections["overwrite"],
        summary_owner_error_detected=detections["owner"],
        summary_loss_total=1,
        summary_overwrite_total=1,
        summary_owner_error_total=1,
    )
    report = ReplayReport(
        case_id="summary_defects",
        backend_pair=("reference", "mutated"),
        metrics=metrics,
        diffs=[diff.to_dict() for diff in all_diffs],
    )
    _write_json_report(tmp_path, "session_memory_summary_defect_report", report)
    assert metrics.summary_loss_detection_rate == 1.0
    assert metrics.summary_overwrite_detection_rate == 1.0
    assert metrics.summary_owner_error_detection_rate == 1.0
    assert metrics.mutation_detection_rate == 1.0


@pytest.mark.replay_lightweight
def test_allowed_diff_requires_explicit_rule():
    reference = {"sessions": [{"session_id": "s1", "state": {"value": 1}}], "memory": [], "summaries": []}
    actual = {"sessions": [{"session_id": "s1", "state": {"value": 2}}], "memory": [], "summaries": []}
    diffs = compare_snapshots(reference, actual, case_id="allowed", backend_pair=("left", "right"))
    assert len(diffs) == 1
    assert not diffs[0].allowed

    allowed = compare_snapshots(
        reference,
        actual,
        case_id="allowed",
        backend_pair=("left", "right"),
        allowed_diff_rules=[
            AllowedDiffRule(
                backend_pair=("left", "right"),
                field_path="$.sessions[0].state.value",
                comparator="exact_path",
                reason="demonstrate explicit allowed_diff plumbing",
                still_validate="path must still be localized",
            )
        ],
    )
    assert len(allowed) == 1
    assert allowed[0].allowed
    assert allowed[0].reason == "demonstrate explicit allowed_diff plumbing"


@pytest.mark.replay_lightweight
def test_diff_report_schema_contains_required_location_fields(tmp_path):
    case = _case_by_id("all_entities_contract")
    left_snapshot, _ = _run(_run_case(case, tmp_path))
    reference = canonicalize_snapshot(left_snapshot)
    mutated = registered_mutations()[0].mutate(reference)
    diffs = compare_snapshots(reference, mutated, case_id="schema", backend_pair=("reference", "mutated"))
    report = ReplayReport(
        case_id="schema",
        backend_pair=("reference", "mutated"),
        metrics=build_metrics(normal_case_count=1, normal_case_pass_count=0, diffs=diffs),
        diffs=[diff.to_dict() for diff in diffs],
    )
    path = tmp_path / "schema_report.json"
    write_json_report(path, report)
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "case_id",
        "backend_pair",
        "session_id",
        "entity_type",
        "entity_id",
        "index",
        "field_path",
        "reference_value",
        "actual_value",
        "allowed",
        "category",
        "reason",
    }
    assert required <= set(payload["diffs"][0])


@pytest.mark.replay_lightweight
def test_sqlite_backend_rebuild_persists_observable_entities(tmp_path):
    case = _case_by_id("all_entities_contract")
    adapter = SQLiteReplayAdapter(case=case, workdir=tmp_path)
    _run(adapter.setup())
    try:
        before = _run(adapter.replay())
        sessions = dict(adapter.sessions)
        event_map = dict(adapter.actual_to_client_event_id)
        probes = list(adapter.memory_probes)
        summaries = {key: list(value) for key, value in adapter.summary_records.items()}
    finally:
        _run(adapter.close())

    rebuilt = SQLiteReplayAdapter(case=case, workdir=tmp_path)
    _run(rebuilt.setup())
    try:
        rebuilt.sessions = sessions
        rebuilt.actual_to_client_event_id = event_map
        rebuilt.memory_probes = probes
        rebuilt.summary_records = summaries
        after = _run(rebuilt.snapshot())
    finally:
        _run(rebuilt.close())

    before_data = canonicalize_snapshot(before)
    after_data = canonicalize_snapshot(after)
    rebuild_diffs = compare_snapshots(
        before_data,
        after_data,
        case_id=case.case_id,
        backend_pair=("sqlite", "sqlite_rebuilt"),
    )
    assert rebuild_diffs == []
    assert any(event["is_summary"] for session in after_data["sessions"] for event in session["events"])
    assert after_data["memory"][0]["memories"]


@pytest.mark.replay_lightweight
def test_fault_injection_proxy_raises_after_committed_operation(tmp_path):
    case = _case_by_id("single_turn_text")
    adapter = InMemoryReplayAdapter(case=case)
    proxy = FaultInjectingAdapter(adapter, fail_after_op_kind="append_text")
    _run(adapter.setup())
    try:
        _run(proxy.apply(case.operations[0]))
        with pytest.raises(ReplayInjectedFault):
            _run(proxy.apply(case.operations[1]))
        snapshot = _run(adapter.snapshot())
        data = canonicalize_snapshot(snapshot)
        assert data["sessions"][0]["events"][0]["event_id"] == "u1"
    finally:
        _run(adapter.close())


@pytest.mark.replay_lightweight
def test_runtime_fault_retry_duplicate_is_detected(tmp_path):
    case = _case_by_id("single_turn_text")
    adapter = InMemoryReplayAdapter(case=case)
    proxy = FaultInjectingAdapter(adapter, fail_after_op_kind="append_text")
    _run(adapter.setup())
    try:
        _run(proxy.apply(case.operations[0]))
        with pytest.raises(ReplayInjectedFault):
            _run(proxy.apply(case.operations[1]))
        _run(adapter.apply(case.operations[1]))
        snapshot = canonicalize_snapshot(_run(adapter.snapshot()))
        diffs = compare_snapshots(
            snapshot,
            snapshot,
            case_id="runtime_fault_retry",
            backend_pair=("runtime", "runtime"),
        )
        categories = {diff.category for diff in diffs if not diff.allowed}
        metrics = build_metrics(
            normal_case_count=1,
            normal_case_pass_count=1,
            diffs=diffs,
            runtime_fault_total=1,
            runtime_fault_detected=1 if "duplicate_event" in categories else 0,
        )
        report = ReplayReport(
            case_id="runtime_fault_retry",
            backend_pair=("runtime", "runtime"),
            metrics=metrics,
            diffs=[diff.to_dict() for diff in diffs],
        )
        _write_json_report(tmp_path, "session_memory_summary_runtime_fault_report", report)
        assert "duplicate_event" in categories
        assert metrics.runtime_fault_detection_rate == 1.0
    finally:
        _run(adapter.close())


def _summary_mutations() -> list[MutationOperator]:
    return [
        mutation for mutation in registered_mutations()
        if mutation.summary_defect in {"loss", "overwrite", "owner"}
    ]


def _delete_first_observable_event(snapshot: dict) -> dict:
    mutated = json.loads(json.dumps(snapshot))
    for session in mutated["sessions"]:
        if session["events"]:
            session["events"].pop(0)
            return mutated
    raise AssertionError("required replay case did not produce an observable event")


def _write_report_pair(tmp_path: Path, stem: str, report: ReplayReport) -> None:
    _write_json_report(tmp_path, stem, report)
    write_markdown_report(tmp_path / f"{stem}.md", report)
    report_dir = os.environ.get("REPLAY_REPORT_DIR")
    if report_dir:
        write_markdown_report(Path(report_dir) / f"{stem}.md", report)


def _write_json_report(tmp_path: Path, stem: str, report: ReplayReport) -> None:
    write_json_report(tmp_path / f"{stem}.json", report)
    report_dir = os.environ.get("REPLAY_REPORT_DIR")
    if report_dir:
        write_json_report(Path(report_dir) / f"{stem}.json", report)
