#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Unit tests for the replay harness report generator."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.sessions.replay_harness._comparator import DiffEntry
from tests.sessions.replay_harness._report import (
    CaseResult,
    DiffReport,
    ReportMetadata,
    ReportSummary,
    _compute_false_positive_rate,
    generate_report,
    report_to_dict,
    write_report,
)


BP = ("in_memory", "sql")


def _case(case_id, status="pass", diffs=None):
    return CaseResult(
        case_id=case_id,
        description=f"Case {case_id}",
        status=status,
        diffs=diffs or [],
    )


def _diff(field_path="events[0].text", value_a="a", value_b="b", allowed=False):
    return DiffEntry(
        backend_pair=BP,
        category="events",
        session_id="s-1",
        event_index=0,
        field_path=field_path,
        value_a=value_a,
        value_b=value_b,
        allowed=allowed,
    )


# ── ReportMetadata ─────────────────────────────────────────────────────


class TestReportMetadata:

    def test_default_run_id_is_non_empty(self):
        m = ReportMetadata()
        assert m.run_id

    def test_default_timestamp_is_non_empty(self):
        m = ReportMetadata()
        assert m.timestamp
        assert "T" in m.timestamp

    def test_backends_recorded(self):
        m = ReportMetadata(backends=["in_memory", "sql"])
        assert m.backends == ["in_memory", "sql"]


# ── CaseResult ─────────────────────────────────────────────────────────


class TestCaseResult:

    def test_default_status_is_pass(self):
        r = CaseResult(case_id="01")
        assert r.status == "pass"

    def test_explicit_status(self):
        r = CaseResult(case_id="01", status="fail")
        assert r.status == "fail"


# ── ReportSummary ──────────────────────────────────────────────────────


class TestReportSummary:

    def test_defaults_are_zero(self):
        s = ReportSummary()
        assert s.total == 0
        assert s.passed == 0
        assert s.failed == 0

    def test_passed_failed_tracked(self):
        s = ReportSummary(total=10, passed=8, failed=2)
        assert s.total == 10
        assert s.passed == 8
        assert s.failed == 2


# ── _compute_false_positive_rate ───────────────────────────────────────


class TestFalsePositiveRate:

    def test_zero_when_no_passing(self):
        results = [_case("01", status="fail")]
        assert _compute_false_positive_rate(results) == 0.0

    def test_zero_when_all_pass_no_diffs(self):
        results = [_case("01", status="pass"), _case("02", status="pass")]
        assert _compute_false_positive_rate(results) == 0.0

    def test_half_when_half_have_allowed_diffs(self):
        results = [
            _case("01", status="pass"),
            _case("02", status="pass", diffs=[_diff(allowed=True)]),
        ]
        assert _compute_false_positive_rate(results) == 0.5

    def test_ignores_failing_cases(self):
        results = [
            _case("01", status="pass", diffs=[_diff(allowed=True)]),
            _case("02", status="fail", diffs=[_diff()]),
        ]
        assert _compute_false_positive_rate(results) == 1.0


# ── generate_report ────────────────────────────────────────────────────


class TestGenerateReport:

    def test_empty_results(self):
        report = generate_report([])
        assert isinstance(report, DiffReport)
        assert report.summary.total == 0

    def test_all_pass(self):
        results = [_case("01"), _case("02")]
        report = generate_report(results, backends=["in_memory", "sql"])
        assert report.summary.total == 2
        assert report.summary.passed == 2
        assert report.summary.failed == 0

    def test_all_fail(self):
        results = [_case("01", status="fail"), _case("02", status="fail")]
        report = generate_report(results)
        assert report.summary.passed == 0
        assert report.summary.failed == 2

    def test_mixed_statuses(self):
        results = [
            _case("01", status="pass"),
            _case("02", status="fail"),
            _case("03", status="error"),
        ]
        report = generate_report(results)
        assert report.summary.passed == 1
        assert report.summary.failed == 1
        assert report.summary.error == 1

    def test_metadata_present(self):
        report = generate_report([], backends=["in_memory", "sql"])
        assert report.metadata.run_id
        assert report.metadata.timestamp
        assert report.metadata.backends == ["in_memory", "sql"]

    def test_summary_counts_accurate(self):
        results = [_case("01"), _case("02", status="fail")]
        report = generate_report(results)
        assert report.summary.total == 2
        assert report.summary.passed == 1
        assert report.summary.failed == 1

    def test_diff_entries_in_results(self):
        results = [_case("01", diffs=[_diff()])]
        report = generate_report(results)
        assert len(report.results[0].diffs) == 1
        assert report.results[0].diffs[0].field_path == "events[0].text"


# ── report_to_dict ─────────────────────────────────────────────────────


class TestReportSerialization:

    def test_json_serializable(self):
        report = generate_report([_case("01")], backends=["in_memory"])
        d = report_to_dict(report)
        assert isinstance(d, dict)
        assert d["metadata"]["run_id"] == report.metadata.run_id
        assert d["summary"]["total"] == 1

    def test_round_trip(self):
        report = generate_report([_case("01", diffs=[_diff()])], backends=["in_memory", "sql"])
        d = report_to_dict(report)
        assert d["results"][0]["diffs"][0]["field_path"] == "events[0].text"


# ── write_report ───────────────────────────────────────────────────────


class TestWriteReport:

    def test_write_report_creates_file(self):
        report = generate_report([_case("01")], backends=["in_memory"])
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            write_report(report, tmp_path)
            assert tmp_path.exists()
            with open(tmp_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            assert data["metadata"]["run_id"] == report.metadata.run_id
        finally:
            tmp_path.unlink(missing_ok=True)
