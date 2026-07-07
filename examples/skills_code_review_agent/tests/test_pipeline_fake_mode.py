"""Integration tests — full pipeline in fake/dry-run mode."""

import json
import os
import tempfile
import time

import pytest

# Ensure path
import sys
from pathlib import Path
_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.config import load_config
from pipeline.diff_parser import parse_diff, summarize_diff
from pipeline.filter_chain import FilterChain
from pipeline.sandbox import execute_in_sandbox
from pipeline.scanners import run_scanners
from pipeline.dedup import deduplicate, separate_low_confidence
from pipeline.redaction import redact_finding_evidence
from pipeline.report import (
    build_recommendations,
    generate_json_report,
    generate_md_report,
)
from pipeline.telemetry import TelemetryCollector
from pipeline.types import ReviewReport
from storage.dao import ReviewDatabase
from storage.models import (
    FilterLogRecord,
    FindingRecord,
    ReviewTaskRecord,
    SandboxRunRecord,
)


class TestFullPipeline:
    """Complete pipeline integration tests."""

    @pytest.fixture
    def fixtures_dir(self):
        return Path(__file__).resolve().parent.parent / "fixtures" / "diffs"

    def _run_pipeline_for_diff(self, diff_path: str, db_path: str) -> dict:
        """Run complete pipeline and return result summary."""
        with open(diff_path, "r", encoding="utf-8") as f:
            diff_text = f.read()

        cfg = load_config(dry_run=True, db_path=db_path)
        task_id = f"test-{os.path.basename(diff_path)}"
        tel = TelemetryCollector()

        # Stage 1-2: Parse
        files = parse_diff(diff_text)
        if not files:
            return {"status": "no_changes"}

        diff_summary = summarize_diff(files)

        # Stage 3: Filter
        filter_chain = FilterChain()
        filter_decision = filter_chain.evaluate(diff_text)

        # Stage 4: Scan
        all_findings = []
        for f in files:
            if not f.is_binary:
                all_findings.extend(run_scanners(f))

        # Stage 5: Sandbox (skip for pipeline test)
        sandbox_runs = []

        # Stage 6: Dedup + redact
        deduped = deduplicate(all_findings)
        findings, redact_count = redact_finding_evidence(deduped)

        # Stage 7: Report
        high_conf, low_conf = separate_low_confidence(deduped)
        report = ReviewReport(
            task_id=task_id,
            findings=deduped,
            filter_summary={"decision": filter_decision.action},
            sandbox_summary={"total_runs": 0, "failures": 0},
            telemetry=tel.snapshot(),
            human_review_items=low_conf,
            recommendations=build_recommendations(deduped),
        )
        json_report = generate_json_report(report)
        md_report = generate_md_report(report)

        # Stage 8: DB
        db = ReviewDatabase(db_path)
        db.connect()
        sev = _count_severity(deduped)
        db.insert_task(ReviewTaskRecord(
            task_id=task_id, diff_source=diff_path,
            diff_summary=diff_summary, status="completed",
            files_changed=len(files), total_findings=len(deduped),
            critical_count=sev.get("critical", 0),
            high_count=sev.get("high", 0),
            medium_count=sev.get("medium", 0),
            low_count=sev.get("low", 0),
            info_count=sev.get("info", 0),
        ))
        if deduped:
            db.insert_findings_batch([
                FindingRecord(task_id=task_id, severity=f.severity.value,
                              category=f.category.value, file=f.file,
                              line=f.line, title=f.title,
                              evidence=f.evidence, recommendation=f.recommendation,
                              confidence=f.confidence, source=f.source)
                for f in deduped
            ])
        db.close()

        return {
            "task_id": task_id,
            "files": len(files),
            "findings": len(deduped),
            "filter_action": filter_decision.action,
            "json_report": json_report,
            "md_report": md_report,
            "json_data": json.loads(json_report),
        }

    def test_security_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "security.diff"), temp_db)
        assert result["findings"] >= 4  # os.system, subprocess, eval, pickle
        assert result["filter_action"] == "allow"
        # Verify DB
        db = ReviewDatabase(temp_db)
        db.connect()
        task = db.get_task(result["task_id"])
        assert task is not None
        assert task.total_findings == result["findings"]
        db_findings = db.get_findings_by_task(result["task_id"])
        assert len(db_findings) == result["findings"]
        db.close()

    def test_clean_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "clean.diff"), temp_db)
        assert result["findings"] == 0

    def test_secret_redaction_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "secret_redaction.diff"), temp_db)
        # Should detect multiple secrets
        assert result["findings"] >= 3
        # Findings must not contain the actual secrets
        for f in result["json_data"]["findings"]:
            assert "sk-" not in f["evidence"], f"Secret leaked: {f['evidence']}"

    def test_db_lifecycle_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "db_lifecycle.diff"), temp_db)
        # Should find cursor creation and missing commit
        assert result["findings"] >= 2

    def test_async_resource_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "async_resource_leak.diff"), temp_db)
        assert result["findings"] >= 2  # time.sleep + open without close

    def test_missing_tests_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "missing_tests.diff"), temp_db)
        assert result["findings"] >= 2  # two new functions without tests

    def test_duplicate_finding_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "duplicate_finding.diff"), temp_db)
        # 3 os.system calls — but dedup by (file, line, category, title)
        # Since they're on different lines, they should all be kept
        assert result["findings"] >= 3

    def test_sandbox_failure_diff(self, fixtures_dir, temp_db):
        result = self._run_pipeline_for_diff(
            str(fixtures_dir / "sandbox_failure.diff"), temp_db)
        # Should not crash — pipeline handles it gracefully
        assert "findings" in result

    def test_all_8_fixtures(self, fixtures_dir, temp_db):
        """Acceptance: all 8 fixtures must complete without error."""
        for diff_file in sorted(fixtures_dir.glob("*.diff")):
            result = self._run_pipeline_for_diff(str(diff_file), temp_db)
            assert "findings" in result, f"Pipeline failed for {diff_file.name}"

    def test_pipeline_idempotent(self, fixtures_dir, temp_db):
        """Same diff run twice should produce same results."""
        import uuid
        diff_path = str(fixtures_dir / "security.diff")
        # Use different task IDs to avoid UNIQUE constraint
        with open(diff_path, "r", encoding="utf-8") as f:
            diff_text = f.read()
        r1 = self._run_pipeline_for_diff(diff_path, temp_db + "_1")
        r2 = self._run_pipeline_for_diff(diff_path, temp_db + "_2")
        assert r1["findings"] == r2["findings"]

    def test_fake_mode_timing(self, fixtures_dir, temp_db):
        """Fake/dry-run mode should complete in under 2 minutes."""
        start = time.monotonic()
        for diff_file in fixtures_dir.glob("*.diff"):
            self._run_pipeline_for_diff(str(diff_file), temp_db)
        elapsed = time.monotonic() - start
        assert elapsed < 120, f"Fake mode took {elapsed:.1f}s, expected <120s"

    def test_database_has_all_tables(self, temp_db):
        """Database must have all required tables after pipeline run."""
        import sqlite3
        # Initialize DB first
        db = ReviewDatabase(temp_db)
        db.connect()
        db.close()
        # Now check
        conn = sqlite3.connect(temp_db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()
        for expected in ["review_tasks", "findings", "sandbox_runs", "filter_logs"]:
            assert expected in tables, f"Missing table: {expected}"


def _count_severity(findings) -> dict:
    counts = {}
    for f in findings:
        key = f.severity.value
        counts[key] = counts.get(key, 0) + 1
    return counts
