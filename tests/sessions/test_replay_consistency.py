#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Session / Memory / Summary replay consistency tests."""

from __future__ import annotations

import copy
import json
import os
import re
import time

import pytest

from trpc_agent_sdk.sessions import ReplayBackend
from trpc_agent_sdk.sessions import ReplayCase
from trpc_agent_sdk.sessions import ReplayCaseError
from trpc_agent_sdk.sessions import ReplayComparator
from trpc_agent_sdk.sessions import ReplayHarness
from trpc_agent_sdk.sessions import ReplayReport
from trpc_agent_sdk.sessions import ReplayRunResult


def _mutate_snapshot(snapshot, mutation):
    """Inject one acceptance-only defect into a normalized snapshot."""
    mutated = copy.deepcopy(snapshot)
    sessions = mutated["sessions"]
    first_session = sessions[sorted(sessions)[0]]
    if mutation == "event_content":
        first_session["events"][0]["content"]["parts"][0]["text"] += " [mutated]"
    elif mutation == "event_order":
        first_session["events"][0], first_session["events"][1] = (
            first_session["events"][1],
            first_session["events"][0],
        )
    elif mutation == "tool_response":
        for event in first_session["events"]:
            for part in event.get("content", {}).get("parts", []):
                if "function_response" in part:
                    part["function_response"]["response"] = {"temperature": -99}
                    return mutated
        raise ValueError("tool_response mutation requires a function response")
    elif mutation == "state_value":
        first_session["state"]["current_topic"] = "mutated"
    elif mutation == "memory_value":
        first_query = mutated["memory"][sorted(mutated["memory"])[0]]
        first_query["entries"][0]["text"] = "mutated memory"
        first_query["texts"][0] = "mutated memory"
    elif mutation == "summary_missing":
        first_session["summary"] = None
    elif mutation == "summary_overwrite":
        first_session["summary"]["version"] -= 1
        first_session["summary"]["replaces_summary_id"] = "wrong-summary"
    elif mutation == "summary_session":
        first_session["summary"]["session_id"] = "foreign-session"
    elif mutation == "dirty_state":
        first_session["state"]["transaction_status"] = "dirty"
    elif mutation == "duplicate_event":
        first_session["events"].append(copy.deepcopy(first_session["events"][-1]))
        first_session["event_ids"].append(first_session["event_ids"][-1])
    else:
        raise ValueError(f"Unknown mutation {mutation!r}")
    return mutated


def _mutation_for_case(case_id):
    """Return the acceptance-only defect assigned to a public replay case."""
    mutations = {
        "single_turn": "event_content",
        "multi_turn": "event_order",
        "tool_call": "tool_response",
        "state_update": "state_value",
        "memory": "memory_value",
        "summary_create": "summary_missing",
        "summary_update": "summary_overwrite",
        "summary_truncation": "summary_session",
        "write_failure": "dirty_state",
        "duplicate_write": "duplicate_event",
    }
    try:
        return mutations[case_id]
    except KeyError as exc:
        raise AssertionError(f"No acceptance mutation configured for replay case {case_id!r}") from exc


def _build_acceptance_report(run: ReplayRunResult, cases: list[ReplayCase]) -> dict:
    """Add task-specific mutation and quality-gate metrics to the SDK report."""
    report = copy.deepcopy(run.report)
    comparator = ReplayComparator()
    case_by_id = {case.case_id: case for case in cases}
    mutation_checks = []
    normal_comparisons = 0
    false_positive_comparisons = 0

    for case_report in report["cases"]:
        case = case_by_id[case_report["case_id"]]
        category = case.metadata["category"]
        expected_anomaly = case.metadata["expected_anomaly"]
        mutation = _mutation_for_case(case.case_id)
        case_report.pop("metadata")
        case_report["category"] = category
        case_report["expected_anomaly"] = expected_anomaly
        for backend_report in case_report["backends"].values():
            has_defect = bool(
                backend_report["differences_to_expected"] or backend_report["operation_errors"])
            if expected_anomaly:
                backend_report["status"] = (
                    "expected_anomaly_detected" if has_defect else "unexpected_match")
            else:
                backend_report["status"] = "mismatch" if has_defect else "match"
        if not expected_anomaly:
            normal_comparisons += len(case_report["comparisons"])
            false_positive_comparisons += sum(
                comparison["status"] != "match" for comparison in case_report["comparisons"])

        reference = run.backend_results[case.case_id][report["reference_backend"]].normalized
        mutated = _mutate_snapshot(reference, mutation)
        differences = comparator.compare(
            reference,
            mutated,
            backend=f"mutated:{mutation}",
            reference_backend=report["reference_backend"],
        )
        mutation_checks.append({
            "case_id": case.case_id,
            "mutation": mutation,
            "detected": bool(differences),
            "differences": differences,
        })

    detected_mutations = sum(check["detected"] for check in mutation_checks)
    report["mutation_checks"] = mutation_checks
    report["metrics"].update({
        "normal_cases": sum(not case.metadata["expected_anomaly"] for case in cases),
        "anomaly_cases": sum(case.metadata["expected_anomaly"] for case in cases),
        "normal_case_backend_comparisons": normal_comparisons,
        "false_positive_case_backend_comparisons": false_positive_comparisons,
        "false_positive_rate": (
            false_positive_comparisons / normal_comparisons if normal_comparisons else 0.0),
        "mutations": len(mutation_checks),
        "detected_mutations": detected_mutations,
        "mutation_detection_rate": detected_mutations / len(mutation_checks) if mutation_checks else 0.0,
        "summary_defect_detection": {
            check["mutation"]: check["detected"]
            for check in mutation_checks
            if check["mutation"] in {"summary_missing", "summary_overwrite", "summary_session"}
        },
    })
    report["runtime_budget_seconds"] = 30
    return report


@pytest.fixture(scope="module")
def replay_case_dir(pytestconfig):
    """Locate repository-owned replay cases through pytest's portable root path."""
    case_dir = pytestconfig.rootpath / "tests" / "sessions" / "replay_cases"
    if not case_dir.is_dir():
        pytest.fail(f"Replay case directory does not exist: {case_dir}")
    return case_dir


@pytest.fixture(scope="module")
def replay_cases(replay_case_dir):
    """Load the public replay catalog once per test module."""
    return ReplayHarness.load_cases(replay_case_dir)


@pytest.fixture(scope="module")
def repository_report_path(pytestconfig):
    """Locate the committed acceptance report from pytest's project root."""
    report_path = pytestconfig.rootpath / "session_memory_summary_diff_report.json"
    if not report_path.is_file():
        pytest.fail(f"Committed replay report does not exist: {report_path}")
    return report_path


@pytest.fixture(scope="module")
async def lightweight_run(tmp_path_factory, replay_cases):
    """Run the complete lightweight matrix once for the module."""
    work_dir = tmp_path_factory.mktemp("replay-lightweight")
    started = time.perf_counter()
    run = await ReplayHarness.create_lightweight(
        work_dir=work_dir,
        run_prefix="replay_baseline",
    ).run(replay_cases)
    elapsed = time.perf_counter() - started
    report = _build_acceptance_report(run, replay_cases)
    report_path = work_dir / "session_memory_summary_diff_report.json"
    ReplayReport.write(report, report_path)
    yield report, elapsed, report_path, work_dir


class TestReplayCaseValidation:
    """Test replay case discovery and JSONL validation."""

    def test_replay_case_catalog_has_ten_validated_cases(self, replay_cases):
        assert len(replay_cases) == 10
        assert len({case.case_id for case in replay_cases}) == 10
        assert sum(not case.metadata["expected_anomaly"] for case in replay_cases) == 8
        assert sum(case.metadata["expected_anomaly"] for case in replay_cases) == 2
        assert all("mutation" not in case.metadata for case in replay_cases)
        assert {operation["op"] for case in replay_cases for operation in case.operations} == {
            "create_session",
            "append_event",
            "store_memory",
            "search_memory",
            "summarize",
            "inject_failure",
            "checkpoint",
        }

    @pytest.mark.parametrize(
        ("contents", "message"),
        [
            ('{"op":"append_event"}\n', "first operation must be 'case'"),
            (
                '{"op":"case","case_id":"bad","category":"normal","expected_anomaly":false,'
                '"mutation":"event_content","expected":{}}\n'
                '{"op":"unknown","operation_id":"bad-op"}\n',
                "unknown operation",
            ),
            (
                '{"op":"case","case_id":"bad","category":"normal","expected_anomaly":false,'
                '"mutation":"event_content","expected":{}}\n'
                '{"op":"create_session","operation_id":"same","session_id":"one"}\n'
                '{"op":"create_session","operation_id":"same","session_id":"two"}\n',
                "duplicate operation_id",
            ),
            (
                '{"op":"case","case_id":"bad","category":"normal","expected_anomaly":false,'
                '"mutation":"event_content","expected":{}}\n'
                '{"op":"create_session","operation_id":"create","session_id":"one"}\n'
                '{"op":"append_event","operation_id":"event-one","session_id":"one",'
                '"event":{"id":"same-event","invocation_id":"one","author":"user","timestamp":1,'
                '"content":{"role":"user","parts":[{"text":"one"}]}}}\n'
                '{"op":"append_event","operation_id":"event-two","session_id":"one",'
                '"event":{"id":"same-event","invocation_id":"two","author":"user","timestamp":2,'
                '"content":{"role":"user","parts":[{"text":"two"}]}}}\n',
                "duplicate event identifier",
            ),
        ],
    )
    def test_jsonl_validation_reports_file_and_line(self, tmp_path, contents, message):
        case_path = tmp_path / "invalid.jsonl"
        case_path.write_text(contents, encoding="utf-8")

        with pytest.raises(ReplayCaseError, match=message) as exc_info:
            ReplayCase.load(case_path)

        assert str(case_path) in str(exc_info.value)
        assert re.search(r":\d+:", str(exc_info.value))

class TestReplayModes:
    """Test in-memory and lightweight replay modes."""

    async def test_in_memory_mode_requires_no_persistent_service(self, replay_cases):
        run = await ReplayHarness(
            backends=[ReplayBackend.in_memory()],
            reference_backend="in_memory",
            profile="in-memory",
            run_prefix="replay_in_memory",
        ).run(replay_cases)
        report = _build_acceptance_report(run, replay_cases)

        assert report["backends"] == ["in_memory"]
        assert report["metrics"]["total_cases"] == 10
        assert report["metrics"]["detected_mutations"] == 10
        assert all(
            backend["status"] == "match"
            for case in report["cases"]
            if not case["expected_anomaly"]
            for backend in case["backends"].values())

    def test_lightweight_matrix_matches_baseline_and_runtime_budget(self, lightweight_run,
                                                                    repository_report_path):
        report, elapsed, report_path, _ = lightweight_run
        baseline = json.loads(repository_report_path.read_text(encoding="utf-8"))

        assert elapsed < report["runtime_budget_seconds"]
        assert report["backends"] == ["in_memory", "sqlite"]
        assert report["metrics"]["false_positive_rate"] <= 0.05
        assert report["metrics"]["normal_case_backend_comparisons"] == 8
        assert report["metrics"]["false_positive_case_backend_comparisons"] == 0
        assert json.loads(report_path.read_text(encoding="utf-8")) == baseline
        assert ReplayReport.dumps(report) == ReplayReport.dumps(baseline)


class TestReplayConsistency:
    """Test mutation detection and allowed-difference boundaries."""

    def test_all_public_mutations_are_detected_with_actionable_locations(self, lightweight_run):
        report, _, _, _ = lightweight_run
        checks = report["mutation_checks"]

        assert len(checks) == 10
        assert all(check["detected"] for check in checks)
        for check in checks:
            for difference in check["differences"]:
                assert difference["backend"] == f"mutated:{check['mutation']}"
                assert difference["path"].startswith("/")
                assert "reference_value" in difference
                assert "backend_value" in difference
                assert difference["allowed"] is False
                if "/events/" in difference["path"]:
                    assert isinstance(difference["event_index"], int)

    def test_summary_loss_overwrite_and_ownership_detection_is_complete(self, lightweight_run):
        report, _, _, _ = lightweight_run
        summary_detection = report["metrics"]["summary_defect_detection"]

        assert summary_detection == {
            "summary_missing": True,
            "summary_overwrite": True,
            "summary_session": True,
        }
        for check in report["mutation_checks"]:
            if check["mutation"] not in summary_detection:
                continue
            assert check["differences"]
            assert all(difference["summary_id"] for difference in check["differences"])
            assert any("/summary" in difference["path"] for difference in check["differences"])

    def test_allowed_diff_is_backend_and_memory_path_scoped(self, lightweight_run):
        report, _, _, _ = lightweight_run
        allowed = [
            item
            for case in report["cases"]
            for backend in case["backends"].values()
            for item in backend["allowed_diff"]
        ]

        assert allowed
        assert all(item["backend"] in {"in_memory", "sqlite"} for item in allowed)
        assert all(item["path"].startswith("/memory/") for item in allowed)
        assert all("/summary" not in item["path"] for item in allowed)

        reference = {
            "sessions": {
                "s": {
                    "summary": {
                        "summary_id": "sum-1",
                        "session_id": "s",
                        "version": 2,
                        "updated_at": 10,
                        "replaces_summary_id": "sum-0",
                    }
                }
            }
        }
        candidate = {
            "sessions": {
                "s": {
                    "summary": {
                        "summary_id": "sum-1",
                        "session_id": "other",
                        "version": 1,
                        "updated_at": 9,
                        "replaces_summary_id": None,
                    }
                }
            }
        }
        differences = ReplayComparator().compare(
            reference,
            candidate,
            backend="candidate",
            reference_backend="reference",
        )
        assert {difference["path"] for difference in differences} == {
            "/sessions/s/summary/session_id",
            "/sessions/s/summary/version",
            "/sessions/s/summary/updated_at",
            "/sessions/s/summary/replaces_summary_id",
        }
        assert all(difference["summary_id"] == "sum-1" for difference in differences)


class TestReplayReport:
    """Test report schema and diagnostic locations."""

    def test_report_schema_and_recovery_differences_are_locatable(self, lightweight_run):
        report, _, _, _ = lightweight_run

        assert report["schema_version"] == 1
        assert report["reference_backend"] == "in_memory"
        assert report["normalization_policy"]
        recovery_cases = [case for case in report["cases"] if case["expected_anomaly"]]
        assert {case["case_id"] for case in recovery_cases} == {"write_failure", "duplicate_write"}
        differences = [
            difference
            for case in recovery_cases
            for backend in case["backends"].values()
            for difference in backend["differences_to_expected"]
        ]
        assert differences
        assert all(difference["backend"] for difference in differences)
        assert all(difference["path"].startswith("/") for difference in differences)
        assert any(difference.get("event_index") is not None or difference["session_id"] for difference in differences)


class TestReplayBackendLifecycle:
    """Test backend cleanup and opt-in integration behavior."""

    def test_sqlite_resources_are_closed_after_run(self, lightweight_run):
        _, _, _, work_dir = lightweight_run
        sqlite_path = work_dir / "replay.sqlite3"
        moved_path = work_dir / "replay.closed.sqlite3"

        sqlite_path.rename(moved_path)
        moved_path.rename(sqlite_path)

    async def test_external_integration_backends_are_opt_in(self, replay_cases):
        harness = ReplayHarness.create_integration(
            sql_url=os.getenv("TRPC_AGENT_REPLAY_SQL_URL"),
            redis_url=os.getenv("TRPC_AGENT_REPLAY_REDIS_URL"),
            run_prefix=f"replay_integration_{time.time_ns()}",
        )
        configured = harness.integration_backend_names()
        if not configured:
            pytest.skip("Set TRPC_AGENT_REPLAY_SQL_URL and/or TRPC_AGENT_REPLAY_REDIS_URL")

        report = _build_acceptance_report(await harness.run(replay_cases), replay_cases)
        assert set(report["backends"]) == {"in_memory", *configured}
        assert report["metrics"]["detected_mutations"] == 10
        assert all(
            comparison["status"] == "match"
            for case in report["cases"]
            if not case["expected_anomaly"]
            for comparison in case["comparisons"])
