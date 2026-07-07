"""Tests for storage/dao module."""

import os

import pytest

from storage.dao import ReviewDatabase
from storage.models import (
    FilterLogRecord,
    FindingRecord,
    ReviewTaskRecord,
    SandboxRunRecord,
)


@pytest.fixture
def db(temp_db):
    """Create a test database with schema initialized."""
    database = ReviewDatabase(temp_db)
    database.connect()
    yield database
    database.close()


class TestTaskOperations:
    """Review task CRUD."""

    def test_insert_and_get_task(self, db):
        task = ReviewTaskRecord(
            task_id="test-001",
            diff_source="test.diff",
            diff_summary="1 file changed",
            status="completed",
            files_changed=1,
            total_findings=3,
            critical_count=1,
            high_count=1,
            medium_count=1,
            duration_ms=1500,
        )
        db.insert_task(task)
        retrieved = db.get_task("test-001")
        assert retrieved is not None
        assert retrieved.task_id == "test-001"
        assert retrieved.total_findings == 3
        assert retrieved.critical_count == 1

    def test_get_nonexistent_task(self, db):
        result = db.get_task("nonexistent")
        assert result is None


class TestFindingOperations:
    """Finding CRUD."""

    def test_insert_single_finding(self, db):
        # Need a task first
        db.insert_task(ReviewTaskRecord(task_id="t1"))
        fid = db.insert_finding(FindingRecord(
            task_id="t1", severity="high", category="security",
            file="a.py", line=5, title="SQL injection",
            confidence=0.9, source="test",
        ))
        assert fid > 0

    def test_batch_insert_findings(self, db):
        db.insert_task(ReviewTaskRecord(task_id="t1"))
        findings = [
            FindingRecord(task_id="t1", severity="high", category="security",
                          file="a.py", line=i, title=f"Issue {i}",
                          confidence=0.9, source="test")
            for i in range(10)
        ]
        count = db.insert_findings_batch(findings)
        assert count == 10

    def test_get_findings_by_task(self, db):
        db.insert_task(ReviewTaskRecord(task_id="t2"))
        db.insert_finding(FindingRecord(
            task_id="t2", severity="critical", category="secret_info",
            file="config.py", line=3, title="API key exposed",
            confidence=0.98, source="test",
        ))
        db.insert_finding(FindingRecord(
            task_id="t2", severity="low", category="missing_tests",
            file="utils.py", line=10, title="Missing test",
            confidence=0.5, source="test",
        ))
        results = db.get_findings_by_task("t2")
        assert len(results) == 2
        # Should be sorted: critical before low
        assert results[0]["severity"] == "critical"
        assert results[1]["severity"] == "low"


class TestSandboxRunOperations:
    """Sandbox run CRUD."""

    def test_insert_sandbox_run(self, db):
        db.insert_task(ReviewTaskRecord(task_id="t1"))
        rid = db.insert_sandbox_run(SandboxRunRecord(
            task_id="t1", command="python scan.py",
            exit_code=0, stdout="OK", duration_ms=500,
        ))
        assert rid > 0

    def test_get_sandbox_runs(self, db):
        db.insert_task(ReviewTaskRecord(task_id="t1"))
        db.insert_sandbox_run(SandboxRunRecord(
            task_id="t1", command="cmd1", exit_code=0, duration_ms=100,
        ))
        db.insert_sandbox_run(SandboxRunRecord(
            task_id="t1", command="cmd2", exit_code=1, duration_ms=200,
        ))
        runs = db.get_sandbox_runs_by_task("t1")
        assert len(runs) == 2

    def test_timed_out_recorded(self, db):
        db.insert_task(ReviewTaskRecord(task_id="t1"))
        db.insert_sandbox_run(SandboxRunRecord(
            task_id="t1", command="slow", exit_code=-1,
            timed_out=True, duration_ms=30000,
        ))
        runs = db.get_sandbox_runs_by_task("t1")
        assert runs[0]["timed_out"] == 1


class TestFilterLogOperations:
    """Filter log CRUD."""

    def test_insert_filter_log(self, db):
        db.insert_task(ReviewTaskRecord(task_id="t1"))
        lid = db.insert_filter_log(FilterLogRecord(
            task_id="t1", action="deny",
            reason="Dangerous command detected",
            filter_name="dangerous_commands",
        ))
        assert lid > 0

    def test_get_filter_logs(self, db):
        db.insert_task(ReviewTaskRecord(task_id="t1"))
        db.insert_filter_log(FilterLogRecord(
            task_id="t1", action="deny", reason="reason1",
        ))
        logs = db.get_filter_logs_by_task("t1")
        assert len(logs) == 1


class TestFullReport:
    """Full task report with all related records."""

    def test_complete_report(self, db):
        db.insert_task(ReviewTaskRecord(
            task_id="full-001", diff_source="test", total_findings=2,
            critical_count=1, high_count=1, files_changed=1,
            sandbox_runs=1, filter_intercepts=1,
        ))
        db.insert_finding(FindingRecord(
            task_id="full-001", severity="critical", category="security",
            file="a.py", line=1, title="Issue", confidence=0.9, source="test",
        ))
        db.insert_sandbox_run(SandboxRunRecord(
            task_id="full-001", command="scan", exit_code=0,
        ))
        db.insert_filter_log(FilterLogRecord(
            task_id="full-001", action="allow", reason="clean",
        ))

        report = db.get_task_full_report("full-001")
        assert "error" not in report
        assert report["task"]["findings_summary"]["total"] == 2
        assert len(report["findings"]) == 1
        assert len(report["sandbox_runs"]) == 1
        assert len(report["filter_logs"]) == 1

    def test_missing_task(self, db):
        report = db.get_task_full_report("nonexistent")
        assert "error" in report
