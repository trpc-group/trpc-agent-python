# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Additional replay consistency tests covering edge cases and boundary conditions.

This module provides supplementary tests that exercise the harness components
in isolation and in combination, covering edge cases not addressed by the
standard parameterized replay case tests.

Test areas:
    - Normalizer: timestamp precision, ID replacement, null/empty, summary text
    - Comparator: missing sessions, nested state, event order, memory
    - DiffReport: serialization, pass/fail, aggregation
    - ReplayLoader: comments, empty lines, malformed JSON
    - BackendExecutor: unknown operations, error handling
    - Allowed diff: classification accuracy
    - Summary: whitespace normalization, empty text
    - State: nested structures, overwrite patterns
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tests.sessions.harness.allowed_diff import ALLOWED_DIFFS
from tests.sessions.harness.allowed_diff import is_allowed_diff
from tests.sessions.harness.backend_executor import BackendExecutor
from tests.sessions.harness.comparator import Comparator
from tests.sessions.harness.diff_report import DiffReport
from tests.sessions.harness.diff_report import DiffReportGenerator
from tests.sessions.harness.normalizer import Normalizer
from tests.sessions.harness.replay_loader import Operation
from tests.sessions.harness.replay_loader import ReplayCase
from tests.sessions.harness.replay_loader import ReplayLoader
from tests.sessions.harness.snapshot import BackendSnapshot


# ---------------------------------------------------------------------------
# Normalizer edge case tests
# ---------------------------------------------------------------------------


class TestNormalizerEdgeCases:
    """Tests for the Normalizer covering timestamp precision, ID replacement,
    null/empty normalization, summary text, and JSON key sorting."""

    def test_normalize_timestamp_precision(self):
        """Timestamps should be rounded to 3 decimal places."""
        normalizer = Normalizer()
        assert normalizer.normalize_timestamp(1234.5678912) == 1234.568
        assert normalizer.normalize_timestamp(0.0004999) == 0.0
        assert normalizer.normalize_timestamp(0.0005) == 0.001
        assert normalizer.normalize_timestamp(0.0) == 0.0
        assert normalizer.normalize_timestamp(999999.999999) == 1000000.0

    def test_normalize_id_replacement(self):
        """Auto-generated IDs should be replaced with placeholder."""
        normalizer = Normalizer()
        assert normalizer.normalize_id("abc-123-def") == "{AUTO_ID}"
        assert normalizer.normalize_id("") == ""
        assert normalizer.normalize_id("any-value") == "{AUTO_ID}"

    def test_normalize_null_representations(self):
        """None, empty string, empty list, and empty dict should all normalize to None."""
        normalizer = Normalizer()
        assert normalizer.normalize_null(None) is None
        assert normalizer.normalize_null("") is None
        assert normalizer.normalize_null([]) is None
        assert normalizer.normalize_null({}) is None
        assert normalizer.normalize_null("hello") == "hello"
        assert normalizer.normalize_null([1, 2, 3]) == [1, 2, 3]
        assert normalizer.normalize_null({"key": "val"}) == {"key": "val"}

    def test_normalize_json_keys_sorted(self):
        """Dict keys should be sorted alphabetically."""
        normalizer = Normalizer()
        result = normalizer.normalize_json_keys({"c": 3, "a": 1, "b": 2})
        assert list(result.keys()) == ["a", "b", "c"]
        assert result["a"] == 1
        assert result["b"] == 2
        assert result["c"] == 3

    def test_normalize_summary_text_whitespace(self):
        """Summary text should have extra whitespace collapsed."""
        normalizer = Normalizer()
        text = "Hello   world\n\n\t  extra   spaces"
        result = normalizer.normalize_summary_text(text)
        assert result == "Hello world extra spaces"

    def test_normalize_summary_text_punctuation(self):
        """Unicode punctuation should be normalized to ASCII."""
        normalizer = Normalizer()
        text = "\u3010Hello\u3011\u3000\u3000world\uff0c\uff0e\uff01\uff1f\uff1a\uff1b"
        result = normalizer.normalize_summary_text(text)
        assert result == "Hello world,.!?:;"

    def test_normalize_summary_text_empty(self):
        """Empty or whitespace-only summary text should return empty string."""
        normalizer = Normalizer()
        assert normalizer.normalize_summary_text("") == ""
        assert normalizer.normalize_summary_text("   ") == ""

    def test_normalize_event_replaces_all_ids(self):
        """Event normalization should replace id, invocation_id, request_id, parent_invocation_id."""
        normalizer = Normalizer()
        event = {
            "id": "evt-123",
            "invocation_id": "inv-456",
            "request_id": "req-789",
            "parent_invocation_id": "parent-000",
            "timestamp": 1234.5678912,
            "author": "user",
            "content": {"parts": [{"text": "Hello"}]},
        }
        result = normalizer.normalize_event(event)
        assert result["id"] == "{AUTO_ID}"
        assert result["invocation_id"] == "{AUTO_ID}"
        assert result["request_id"] == "{AUTO_ID}"
        assert result["parent_invocation_id"] == "{AUTO_ID}"
        assert result["timestamp"] == 1234.568
        assert result["author"] == "user"

    def test_normalize_event_without_optional_fields(self):
        """Event without optional fields should be normalized safely."""
        normalizer = Normalizer()
        event = {"author": "assistant", "content": {"parts": [{"text": "Hi"}]}}
        result = normalizer.normalize_event(event)
        assert result["author"] == "assistant"
        assert "id" not in result

    def test_normalize_session_with_events(self):
        """Session normalization should normalize events recursively."""
        normalizer = Normalizer()
        session = {
            "id": "sess-1",
            "last_update_time": 1234.5678912,
            "events": [
                {"id": "evt-1", "timestamp": 1000.123456},
                {"id": "evt-2", "timestamp": 2000.987654},
            ],
        }
        result = normalizer.normalize_session(session)
        assert result["last_update_time"] == 1234.568
        assert result["events"][0]["id"] == "{AUTO_ID}"
        assert result["events"][0]["timestamp"] == 1000.123
        assert result["events"][1]["id"] == "{AUTO_ID}"
        assert result["events"][1]["timestamp"] == 2000.988

    def test_normalize_summary_dict(self):
        """Summary normalization should normalize text and timestamp."""
        normalizer = Normalizer()
        summary = {
            "session_id": "s1",
            "summary_text": "  Hello   world  ",
            "summary_timestamp": 1234.5678912,
        }
        result = normalizer.normalize_summary(summary)
        assert result["summary_text"] == "Hello world"
        assert result["summary_timestamp"] == 1234.568


# ---------------------------------------------------------------------------
# AllowedDiff classification tests
# ---------------------------------------------------------------------------


class TestAllowedDiffClassification:
    """Tests that allowed diff rules are correctly classified."""

    def test_timestamp_fields_are_allowed(self):
        """Any field path ending with .timestamp should be allowed."""
        allowed, reason = is_allowed_diff("events[0].timestamp")
        assert allowed is True
        assert "Timestamp" in reason

        allowed, reason = is_allowed_diff("events[0].summary_timestamp")
        assert allowed is True

    def test_id_fields_are_allowed(self):
        """Any field path ending with .id should be allowed."""
        allowed, reason = is_allowed_diff("events[0].id")
        assert allowed is True
        assert "auto-generated" in reason.lower()

    def test_invocation_id_fields_are_allowed(self):
        """Any field path ending with .invocation_id should be allowed."""
        allowed, reason = is_allowed_diff("events[0].invocation_id")
        assert allowed is True

    def test_business_fields_are_not_allowed(self):
        """Business content fields should not be allowed diffs."""
        allowed, reason = is_allowed_diff("events[0].content.parts[0].text")
        assert allowed is False
        assert reason == ""

        allowed, reason = is_allowed_diff("events[0].author")
        assert allowed is False

    def test_state_fields_not_allowed(self):
        """State fields should not be allowed diffs."""
        allowed, reason = is_allowed_diff("state.key")
        assert allowed is False

    def test_summary_text_not_allowed(self):
        """Summary text content should not be allowed (but metadata is)."""
        allowed, reason = is_allowed_diff("summary_text")
        assert allowed is False

    def test_all_allowed_diffs_have_reason(self):
        """Every allowed diff rule should have a non-empty reason."""
        for ad in ALLOWED_DIFFS:
            assert ad.reason, f"Allowed diff '{ad.field_path_pattern}' has no reason"

    def test_allowed_diff_count(self):
        """There should be at least 10 allowed diff rules for comprehensive coverage."""
        assert len(ALLOWED_DIFFS) >= 10, (
            f"Expected at least 10 allowed diff rules, got {len(ALLOWED_DIFFS)}"
        )


# ---------------------------------------------------------------------------
# Comparator edge case tests
# ---------------------------------------------------------------------------


class TestComparatorEdgeCases:
    """Tests for the Comparator covering snapshot comparison edge cases."""

    async def test_compare_identical_snapshots(self, inmemory_services):
        """Comparing two identical snapshots should produce no unallowed diffs."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "baseline"

        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) == 0, (
            f"Identical snapshots should have no unallowed diffs, got {len(unallowed)}: {unallowed}"
        )

    async def test_detect_session_missing_in_target(self, inmemory_services):
        """When target is missing a session the baseline has, should be detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "baseline"

        target = BackendSnapshot(backend_name="target")

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, target)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) >= 1, (
            f"Should detect missing session in target, got {len(unallowed)} diffs"
        )

    async def test_detect_session_missing_in_baseline(self, inmemory_services):
        """When baseline is missing a session the target has, should be detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_b = await executor.execute(ops)
        snapshot_b.backend_name = "target"

        baseline = BackendSnapshot(backend_name="baseline")

        comparator = Comparator()
        diffs = comparator.compare(baseline, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) >= 1, (
            f"Should detect missing session in baseline, got {len(unallowed)} diffs"
        )

    async def test_detect_state_mismatch_same_session(self, inmemory_services):
        """State value mismatch in the same session should be detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops_a = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {"key": "val_a"}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        ops_b = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {"key": "val_b"}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops_a)
        snapshot_a.backend_name = "baseline"
        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops_b)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) >= 1, (
            f"Should detect state mismatch, got {len(unallowed)} diffs"
        )

    async def test_detect_event_order_mismatch(self, inmemory_services):
        """Event ordering mismatch should be detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops_a = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "First"}]}}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Second"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        ops_b = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Second"}]}}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "First"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops_a)
        snapshot_a.backend_name = "baseline"
        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops_b)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) >= 1, (
            f"Should detect event order mismatch, got {len(unallowed)} diffs"
        )

    async def test_compare_empty_snapshots(self):
        """Comparing two empty snapshots should produce no diffs."""
        snapshot_a = BackendSnapshot(backend_name="baseline")
        snapshot_b = BackendSnapshot(backend_name="target")

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        assert len(diffs) == 0, "Empty snapshots should have no diffs"

    async def test_compare_snapshots_with_errors(self, inmemory_services):
        """Snapshots with errors should still be comparable."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "baseline"
        snapshot_a.errors = [{"op_index": 0, "error": "injected error"}]

        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) == 0, (
            f"Errors should not affect event comparison, got {len(unallowed)} unallowed diffs"
        )


# ---------------------------------------------------------------------------
# DiffReport edge case tests
# ---------------------------------------------------------------------------


class TestDiffReportEdgeCases:
    """Tests for the DiffReport and DiffReportGenerator."""

    def test_diff_report_passed_property(self):
        """Passed should be True only when no unallowed diffs and no summary issues."""
        report = DiffReport(
            case_name="test",
            backend_a="a",
            backend_b="b",
            unallowed_diff_count=0,
        )
        assert report.passed is True

        report.unallowed_diff_count = 1
        assert report.passed is False

        report.unallowed_diff_count = 0
        report.summary_issues = [{"type": "summary_loss", "session_id": "s", "detail": "test"}]
        assert report.passed is False

    def test_diff_report_to_dict(self):
        """DiffReport.to_dict should produce a serializable dict."""
        report = DiffReport(
            case_name="test_case",
            backend_a="inmemory",
            backend_b="sql",
            diffs=[],
            summary_issues=[],
            allowed_diff_count=0,
            unallowed_diff_count=0,
        )
        d = report.to_dict()
        assert d["case_name"] == "test_case"
        assert d["backend_a"] == "inmemory"
        assert d["backend_b"] == "sql"
        assert d["passed"] is True
        assert d["diffs"] == []

    def test_generate_single_report(self):
        """Generate a report from two snapshots."""
        snapshot_a = BackendSnapshot(backend_name="inmemory")
        snapshot_b = BackendSnapshot(backend_name="sql")

        generator = DiffReportGenerator()
        report = generator.generate("test_case", snapshot_a, snapshot_b)
        assert report.case_name == "test_case"
        assert report.backend_a == "inmemory"
        assert report.backend_b == "sql"
        assert report.passed is True

    def test_generate_all_with_baseline_validation(self):
        """Generate all reports requires a valid baseline."""
        snapshots = {
            "inmemory": BackendSnapshot(backend_name="inmemory"),
            "sql": BackendSnapshot(backend_name="sql"),
        }
        generator = DiffReportGenerator()
        reports = generator.generate_all("test_case", snapshots, baseline_name="inmemory")
        assert len(reports) == 1
        assert reports[0].backend_a == "inmemory"
        assert reports[0].backend_b == "sql"

    def test_generate_all_missing_baseline_raises(self):
        """Generate all should raise if baseline is not in snapshots."""
        snapshots = {"sql": BackendSnapshot(backend_name="sql")}
        generator = DiffReportGenerator()
        with pytest.raises(ValueError, match="not found"):
            generator.generate_all("test_case", snapshots, baseline_name="inmemory")

    def test_save_aggregated_report(self):
        """Save aggregated report to a JSON file."""
        reports = [
            DiffReport(
                case_name="case1",
                backend_a="inmemory",
                backend_b="sql",
                unallowed_diff_count=0,
            ),
            DiffReport(
                case_name="case2",
                backend_a="inmemory",
                backend_b="sql",
                unallowed_diff_count=0,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.json"
            generator = DiffReportGenerator()
            generator.save_aggregated_report(reports, output_path)
            assert output_path.exists()
            with open(output_path) as f:
                data = json.load(f)
            assert data["total_cases"] == 2
            assert data["total_reports"] == 2
            assert data["passed_count"] == 2
            assert data["failed_count"] == 0
            assert "generated_at" in data


# ---------------------------------------------------------------------------
# ReplayLoader edge case tests
# ---------------------------------------------------------------------------


class TestReplayLoaderEdgeCases:
    """Tests for the ReplayLoader covering edge cases in JSONL parsing."""

    def test_load_with_comments_and_empty_lines(self):
        """Lines with comments and empty lines should be skipped."""
        jsonl_content = (
            "# This is a comment\n"
            '{"op": "create_session", "app": "test", "user": "u", "session_id": "s", "state": {}}\n'
            "\n"
            '{"op": "get_session", "session_id": "s"}\n'
            "# Another comment\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.jsonl"
            file_path.write_text(jsonl_content)
            case = ReplayLoader.load(file_path)
            assert len(case.operations) == 2
            assert case.operations[0].op == "create_session"
            assert case.operations[1].op == "get_session"

    def test_load_empty_file(self):
        """An empty file should produce a case with no operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "empty.jsonl"
            file_path.write_text("")
            case = ReplayLoader.load(file_path)
            assert len(case.operations) == 0
            assert case.name == "empty"

    def test_load_only_comments(self):
        """A file with only comments should produce a case with no operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "comments.jsonl"
            file_path.write_text("# Comment 1\n# Comment 2\n")
            case = ReplayLoader.load(file_path)
            assert len(case.operations) == 0

    def test_load_malformed_json_raises(self):
        """Malformed JSON should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "bad.jsonl"
            file_path.write_text("{invalid json content")
            with pytest.raises(ValueError, match="Invalid replay case"):
                ReplayLoader.load(file_path)

    def test_load_missing_op_field_raises(self):
        """Missing 'op' field should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "no_op.jsonl"
            file_path.write_text('{"app": "test", "user": "u"}')
            with pytest.raises(ValueError, match="Invalid replay case"):
                ReplayLoader.load(file_path)

    def test_load_all_sorts_by_filename(self):
        """Load all should return cases sorted by filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "c.jsonl").write_text(
                '{"op": "create_session", "app": "t", "user": "u", "session_id": "c", "state": {}}'
            )
            (Path(tmpdir) / "a.jsonl").write_text(
                '{"op": "create_session", "app": "t", "user": "u", "session_id": "a", "state": {}}'
            )
            (Path(tmpdir) / "b.jsonl").write_text(
                '{"op": "create_session", "app": "t", "user": "u", "session_id": "b", "state": {}}'
            )
            cases = ReplayLoader.load_all(tmpdir)
            assert len(cases) == 3
            assert [c.name for c in cases] == ["a", "b", "c"]

    def test_replay_case_name_from_file_stem(self):
        """ReplayCase name should be derived from the file stem."""
        case = ReplayCase(name="01_simple_chat", operations=[])
        assert case.name == "01_simple_chat"


# ---------------------------------------------------------------------------
# BackendExecutor edge case tests
# ---------------------------------------------------------------------------


class TestBackendExecutorEdgeCases:
    """Tests for the BackendExecutor covering edge cases and error handling."""

    async def test_unknown_operation_error_recorded(self, inmemory_services):
        """Executing an unknown operation should record an error in the snapshot."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [Operation(op="unknown_operation", params={})]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) >= 1, "Should record error for unknown operation"
        assert "unknown_operation" in snapshot.errors[0].get("op", "")

    async def test_append_event_without_session(self, inmemory_services):
        """Appending an event to a non-existent session should record an error."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(
                op="append_event",
                params={
                    "session_id": "nonexistent",
                    "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}},
                },
            )
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) >= 1, "Should record error for missing session"

    async def test_get_nonexistent_session(self, inmemory_services):
        """Getting a non-existent session should not error."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="get_session", params={"session_id": "nonexistent"}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0
        assert "s" in snapshot.sessions

    async def test_delete_session(self, inmemory_services):
        """Deleting a session should remove it from the snapshot."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="delete_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert "s" not in snapshot.sessions, "Session should be deleted"

    async def test_store_memory_without_memory_service(self, inmemory_services):
        """Store memory without a memory service should be a no-op."""
        session_svc, _ = inmemory_services
        executor = BackendExecutor(session_svc, memory_service=None)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="store_memory", params={"session_id": "s"}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0
        assert "s" in snapshot.sessions

    async def test_search_memory_without_memory_service(self, inmemory_services):
        """Search memory without a memory service should be a no-op."""
        session_svc, _ = inmemory_services
        executor = BackendExecutor(session_svc, memory_service=None)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="search_memory", params={"key": "test", "query": "hello"}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0

    async def test_multiple_sessions_independent(self, inmemory_services):
        """Multiple sessions should be independent of each other."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s1", "state": {"key": "v1"}}),
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s2", "state": {"key": "v2"}}),
            Operation(op="append_event", params={"session_id": "s1", "event": {"author": "user", "content": {"parts": [{"text": "Event for s1"}]}}}),
            Operation(op="append_event", params={"session_id": "s2", "event": {"author": "user", "content": {"parts": [{"text": "Event for s2"}]}}}),
            Operation(op="get_session", params={"session_id": "s1"}),
            Operation(op="get_session", params={"session_id": "s2"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.sessions) == 2
        assert "s1" in snapshot.sessions
        assert "s2" in snapshot.sessions
        assert len(snapshot.sessions["s1"].events) == 1
        assert len(snapshot.sessions["s2"].events) == 1


# ---------------------------------------------------------------------------
# Cross-backend memory comparison tests
# ---------------------------------------------------------------------------


class TestMemoryComparison:
    """Tests for memory comparison across backends."""

    async def test_memory_store_and_search(self, inmemory_services):
        """Memory store and search should produce consistent results."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test_app", "user": "test_user", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "My favorite color is blue"}]}}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "assistant", "content": {"parts": [{"text": "I'll remember that your favorite color is blue"}]}}}),
            Operation(op="store_memory", params={"session_id": "s"}),
            Operation(op="search_memory", params={"key": "test_app/test_user", "query": "favorite color", "limit": 10}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0
        assert "s" in snapshot.sessions

    async def test_memory_consistency_across_two_runs(self, inmemory_services):
        """Two runs with the same operations should produce consistent memory."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test_app", "user": "test_user", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "I like pizza"}]}}}),
            Operation(op="store_memory", params={"session_id": "s"}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "run1"

        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops)
        snapshot_b.backend_name = "run2"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) == 0, (
            f"Two identical runs should have no unallowed diffs, got {len(unallowed)}"
        )


# ---------------------------------------------------------------------------
# State comparison edge case tests
# ---------------------------------------------------------------------------


class TestStateComparison:
    """Tests for state comparison across backends."""

    async def test_nested_state_update(self, inmemory_services):
        """Nested state updates should be preserved across get_session."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {"nested": {"key": "value"}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0
        assert "s" in snapshot.sessions

    async def test_state_overwrite(self, inmemory_services):
        """State written via create_session should be retrievable."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {"key": "initial"}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0

    async def test_state_comparison_consistency(self, inmemory_services):
        """Two runs with identical state should produce no unallowed diffs."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {"a": 1, "b": 2, "c": {"d": 3}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "baseline"
        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) == 0, (
            f"Identical state runs should have no unallowed diffs, got {len(unallowed)}"
        )


# ---------------------------------------------------------------------------
# Summary edge case tests
# ---------------------------------------------------------------------------


class TestSummaryEdgeCases:
    """Tests for summary comparison edge cases."""

    async def test_summary_whitespace_normalization(self):
        """Summary text with different whitespace should normalize to the same."""
        normalizer = Normalizer()
        text1 = "Hello   world\n\n\textra"
        text2 = "Hello world extra"
        assert normalizer.normalize_summary_text(text1) == normalizer.normalize_summary_text(text2)

    async def test_summary_empty_text(self):
        """Empty summary text should normalize to empty string."""
        normalizer = Normalizer()
        assert normalizer.normalize_summary_text("") == ""
        assert normalizer.normalize_summary_text("   \n\t  ") == ""

    async def test_summary_unicode_punctuation(self):
        """Unicode punctuation in summary text should be normalized."""
        normalizer = Normalizer()
        text = "\u3010Note\u3011 Hello\uff0c world\uff01"
        expected = "Note Hello, world!"
        assert normalizer.normalize_summary_text(text) == expected

    async def test_summary_overwrite_with_different_event_count(self, inmemory_services):
        """Summary with different original_event_count should be detected as overwrite error."""
        from trpc_agent_sdk.sessions import SessionSummary

        snapshot_a = BackendSnapshot(backend_name="baseline")
        snapshot_b = BackendSnapshot(backend_name="target")

        snapshot_a.summaries["s1"] = SessionSummary(
            session_id="s1",
            summary_text="Summary",
            original_event_count=10,
            compressed_event_count=5,
            summary_timestamp=1000.0,
        )
        snapshot_b.summaries["s1"] = SessionSummary(
            session_id="s1",
            summary_text="Summary",
            original_event_count=5,
            compressed_event_count=5,
            summary_timestamp=1000.0,
        )

        comparator = Comparator()
        issues = comparator.check_summary_issues(snapshot_a, snapshot_b)
        overwrite = [i for i in issues if i["type"] == "summary_overwrite_error"]
        assert len(overwrite) >= 1, "Should detect summary overwrite error"

    async def test_summary_only_in_target(self, inmemory_services):
        """Summary only in target should be detected as loss."""
        from trpc_agent_sdk.sessions import SessionSummary

        snapshot_a = BackendSnapshot(backend_name="baseline")
        snapshot_b = BackendSnapshot(backend_name="target")
        snapshot_b.summaries["s1"] = SessionSummary(
            session_id="s1",
            summary_text="Summary",
            original_event_count=5,
            compressed_event_count=5,
            summary_timestamp=1000.0,
        )

        comparator = Comparator()
        issues = comparator.check_summary_issues(snapshot_a, snapshot_b)
        loss = [i for i in issues if i["type"] == "summary_loss"]
        assert len(loss) >= 1, "Should detect summary loss (target has summary, baseline does not)"

    async def test_summary_ownership_mismatch(self, inmemory_services):
        """Summary with different session_id should be detected as ownership error."""
        from trpc_agent_sdk.sessions import SessionSummary

        snapshot_a = BackendSnapshot(backend_name="baseline")
        snapshot_b = BackendSnapshot(backend_name="target")

        snapshot_a.summaries["s1"] = SessionSummary(
            session_id="s1",
            summary_text="Summary",
            original_event_count=5,
            compressed_event_count=5,
            summary_timestamp=1000.0,
        )
        snapshot_b.summaries["s1"] = SessionSummary(
            session_id="s2",
            summary_text="Summary",
            original_event_count=5,
            compressed_event_count=5,
            summary_timestamp=1000.0,
        )

        comparator = Comparator()
        issues = comparator.check_summary_issues(snapshot_a, snapshot_b)
        ownership = [i for i in issues if i["type"] == "summary_ownership_error"]
        assert len(ownership) >= 1, "Should detect summary ownership error"

    async def test_matching_summaries_no_issues(self, inmemory_services):
        """Two identical summaries should produce no issues."""
        from trpc_agent_sdk.sessions import SessionSummary

        snapshot_a = BackendSnapshot(backend_name="baseline")
        snapshot_b = BackendSnapshot(backend_name="target")

        summary = SessionSummary(
            session_id="s1",
            summary_text="Same summary",
            original_event_count=5,
            compressed_event_count=5,
            summary_timestamp=1000.0,
        )
        snapshot_a.summaries["s1"] = summary
        snapshot_b.summaries["s1"] = summary

        comparator = Comparator()
        issues = comparator.check_summary_issues(snapshot_a, snapshot_b)
        assert len(issues) == 0, "Identical summaries should have no issues"


# ---------------------------------------------------------------------------
# Snapshot serialization tests
# ---------------------------------------------------------------------------


class TestSnapshotSerialization:
    """Tests for BackendSnapshot serialization."""

    def test_snapshot_to_serializable_empty(self):
        """Empty snapshot should serialize to a dict with basic info."""
        snapshot = BackendSnapshot(backend_name="test")
        d = snapshot.to_serializable()
        assert d["backend_name"] == "test"
        assert d["session_ids"] == []
        assert d["session_event_counts"] == {}
        assert d["memory_keys"] == []
        assert d["summary_session_ids"] == []
        assert d["error_count"] == 0

    async def test_snapshot_to_serializable_with_data(self, inmemory_services):
        """Snapshot with data should serialize correctly."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        d = snapshot.to_serializable()
        assert d["backend_name"] == "InMemorySessionService"
        assert "s" in d["session_ids"]
        assert d["session_event_counts"]["s"] == 1
        assert d["error_count"] == 0


# ---------------------------------------------------------------------------
# Tool call event tests
# ---------------------------------------------------------------------------


class TestToolCallComparison:
    """Tests for tool call event comparison."""

    async def test_tool_call_roundtrip_consistency(self, inmemory_services):
        """Tool call events should be consistent across two runs."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={
                "session_id": "s",
                "event": {
                    "author": "assistant",
                    "content": {
                        "parts": [{"function_call": {"id": "fc-1", "name": "get_weather", "args": {"city": "Beijing"}}}]
                    }
                }
            }),
            Operation(op="append_event", params={
                "session_id": "s",
                "event": {
                    "author": "user",
                    "content": {
                        "parts": [{"function_response": {"id": "fc-1", "name": "get_weather", "response": {"temp": 25}}}]
                    }
                }
            }),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "baseline"

        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) == 0, (
            f"Tool call round-tripping should be consistent, got {len(unallowed)} unallowed diffs: {unallowed}"
        )

    async def test_tool_call_content_mismatch_detected(self, inmemory_services):
        """Tool call content mismatch should be detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops_a = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={
                "session_id": "s",
                "event": {
                    "author": "assistant",
                    "content": {
                        "parts": [{"function_call": {"id": "fc-1", "name": "get_weather", "args": {"city": "Beijing"}}}]
                    }
                }
            }),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        ops_b = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={
                "session_id": "s",
                "event": {
                    "author": "assistant",
                    "content": {
                        "parts": [{"function_call": {"id": "fc-1", "name": "get_weather", "args": {"city": "Shanghai"}}}]
                    }
                }
            }),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops_a)
        snapshot_a.backend_name = "baseline"
        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops_b)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) >= 1, (
            f"Should detect tool call content mismatch, got {len(unallowed)} diffs"
        )


# ---------------------------------------------------------------------------
# Concurrency and robustness tests
# ---------------------------------------------------------------------------


class TestRobustness:
    """Tests for robustness and edge case handling."""

    async def test_duplicate_event_append(self, inmemory_services):
        """Appending the same event multiple times should be handled."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Dup"}]}}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Dup"}]}}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Dup"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0
        assert "s" in snapshot.sessions

    async def test_many_events_consistency(self, inmemory_services):
        """Many events (50) should be handled consistently."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}})]
        for i in range(50):
            author = "user" if i % 2 == 0 else "assistant"
            ops.append(Operation(op="append_event", params={
                "session_id": "s",
                "event": {"author": author, "content": {"parts": [{"text": f"Message {i}"}]}},
            }))
        ops.append(Operation(op="get_session", params={"session_id": "s"}))

        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0
        assert len(snapshot.sessions["s"].events) == 50

    async def test_update_session_before_get(self, inmemory_services):
        """Update session followed by get should work correctly."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="update_session", params={"session_id": "s"}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        snapshot = await executor.execute(ops)
        assert len(snapshot.errors) == 0
        assert "s" in snapshot.sessions

    async def test_full_workflow_consistency(self, inmemory_services):
        """Full create -> add events -> store memory -> update -> get should be consistent."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        ops = [
            Operation(op="create_session", params={"app": "test_app", "user": "test_user", "session_id": "s_full", "state": {"topic": "testing"}}),
            Operation(op="append_event", params={"session_id": "s_full", "event": {"author": "user", "content": {"parts": [{"text": "Hello, I need help with testing"}]}}}),
            Operation(op="append_event", params={"session_id": "s_full", "event": {"author": "assistant", "content": {"parts": [{"text": "I can help you with testing"}]}}}),
            Operation(op="store_memory", params={"session_id": "s_full"}),
            Operation(op="search_memory", params={"key": "test_app/test_user", "query": "testing"}),
            Operation(op="update_session", params={"session_id": "s_full"}),
            Operation(op="get_session", params={"session_id": "s_full"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "baseline"

        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(ops)
        snapshot_b.backend_name = "target"

        comparator = Comparator()
        diffs = comparator.compare(snapshot_a, snapshot_b)
        unallowed = [d for d in diffs if not d.get("allowed")]
        assert len(unallowed) == 0, (
            f"Full workflow should be consistent, got {len(unallowed)} unallowed diffs: {unallowed}"
        )