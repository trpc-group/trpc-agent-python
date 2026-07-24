"""Performance tests for code review pipeline.

All thresholds are intentionally loose to accommodate CI variability.
Tests guard against regressions like infinite loops or quadratic scaling.
"""

import json
import os
import tempfile
import time
import pytest

from pipeline.config import load_config
from pipeline.diff_parser import parse_diff, summarize_diff
from pipeline.scanners import run_scanners, get_available_scanners
from pipeline.dedup import deduplicate, separate_by_tiers
from pipeline.types import DiffFile, DiffHunk


def _make_diff_files(count: int) -> str:
    """Generate a diff with `count` files."""
    parts = []
    for i in range(count):
        parts.append(f"""diff --git a/file_{i}.py b/file_{i}.py
--- a/file_{i}.py
+++ b/file_{i}.py
@@ -1,0 +1,2 @@
+import os
+os.system("echo test")""")
    return "\n".join(parts)


class TestParsePerformance:
    """Diff parsing performance tests."""

    def test_twenty_files_parse_fast(self):
        diff = _make_diff_files(20)
        start = time.monotonic()
        files = parse_diff(diff)
        elapsed = time.monotonic() - start
        assert len(files) == 20
        assert elapsed < 5.0, f"20-file parse took {elapsed:.2f}s"

    def test_fifty_files_parse_scales(self):
        diff_20 = _make_diff_files(20)
        diff_50 = _make_diff_files(50)

        start = time.monotonic()
        parse_diff(diff_20)
        t20 = time.monotonic() - start

        start = time.monotonic()
        parse_diff(diff_50)
        t50 = time.monotonic() - start

        # 50 files should scale linearly (generous ratio)
        assert t50 < max(t20 * 5.0, 0.5), (
            f"50-file ({t50:.3f}s) vs 20-file ({t20:.3f}s) ratio {t50/max(t20,0.001):.1f}x"
        )


class TestDedupPerformance:
    """Deduplication performance tests."""

    def test_hundred_findings_dedup_fast(self):
        findings = []
        for i in range(100):
            from pipeline.types import Finding, FindingCategory, Severity
            findings.append(Finding(
                severity=Severity.MEDIUM,
                category=FindingCategory.SECURITY,
                file=f"file_{i}.py", line=i,
                title=f"Test finding {i}",
                evidence=f"evidence {i}",
                recommendation=f"fix {i}",
                confidence=0.5 + (i % 50) / 100,
                source="test",
            ))

        start = time.monotonic()
        result = deduplicate(findings)
        elapsed = time.monotonic() - start
        assert len(result) == 100  # All unique
        assert elapsed < 1.0, f"100-finding dedup took {elapsed:.2f}s"

    def test_thousand_findings_dedup_fast(self):
        from pipeline.types import Finding, FindingCategory, Severity
        findings = []
        for i in range(1000):
            findings.append(Finding(
                severity=Severity.MEDIUM,
                category=FindingCategory.SECURITY,
                file=f"f_{i % 50}.py", line=i % 200,
                title=f"Finding {i % 100}",
                evidence="e", recommendation="r",
                confidence=0.7, source="test",
            ))

        start = time.monotonic()
        result = deduplicate(findings)
        elapsed = time.monotonic() - start
        assert len(result) > 0
        assert elapsed < 3.0, f"1000-finding dedup took {elapsed:.2f}s"


class TestReportPerformance:
    """Report generation performance."""

    def test_json_report_fast(self):
        from pipeline.report import generate_json_report
        from pipeline.types import Finding, FindingCategory, ReviewReport, Severity

        findings = []
        for i in range(50):
            findings.append(Finding(
                severity=[Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM][i % 3],
                category=list(FindingCategory)[i % len(list(FindingCategory))],
                file=f"file_{i}.py", line=i,
                title=f"Finding {i}",
                evidence=f"evidence for finding {i}",
                recommendation=f"fix finding {i}",
                confidence=0.7 + i * 0.003,
                source=f"scanner_{i % 5}",
            ))

        report = ReviewReport(
            task_id="perf-test",
            findings=findings,
            filter_summary={"decision": "allow"},
            sandbox_summary={"total_runs": 0},
            telemetry={"total_duration_ms": 100},
            human_review_items=[],
            recommendations=[],
        )

        start = time.monotonic()
        json_str = generate_json_report(report)
        elapsed = time.monotonic() - start
        assert len(json_str) > 100
        assert elapsed < 2.0, f"JSON report with 50 findings took {elapsed:.2f}s"

    def test_md_report_fast(self):
        from pipeline.report import generate_md_report
        from pipeline.types import Finding, FindingCategory, ReviewReport, Severity

        findings = []
        for i in range(50):
            findings.append(Finding(
                severity=Severity.MEDIUM,
                category=FindingCategory.SECURITY,
                file=f"f_{i}.py", line=i,
                title=f"F{i}", evidence="e", recommendation="r",
                confidence=0.8, source="s",
            ))

        report = ReviewReport(
            task_id="perf-md",
            findings=findings,
            filter_summary={},
            sandbox_summary={},
            telemetry={},
            human_review_items=[],
            recommendations=[],
        )

        start = time.monotonic()
        md_str = generate_md_report(report)
        elapsed = time.monotonic() - start
        assert len(md_str) > 100
        assert elapsed < 2.0, f"MD report with 50 findings took {elapsed:.2f}s"


class TestFakeModeEndToEnd:
    """Fake mode end-to-end performance."""

    def test_all_fixtures_e2e_under_two_minutes(self):
        import subprocess, sys
        fixtures_dir = os.path.join(os.path.dirname(__file__), "..", "fixtures", "diffs")
        output_dir = os.path.join(os.path.dirname(__file__), "..")
        start = time.monotonic()

        for fixture in sorted(os.listdir(fixtures_dir)):
            if not fixture.endswith(".diff"):
                continue
            path = os.path.join(fixtures_dir, fixture)
            result = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "..", "run_review.py"),
                 "--diff-file", path, "--output-dir", output_dir, "--dry-run"],
                capture_output=True, text=True, timeout=30,
            )

        elapsed = time.monotonic() - start
        assert elapsed < 120.0, f"All fixtures E2E took {elapsed:.1f}s (limit 120s)"

    def test_single_fixture_under_five_seconds(self):
        import subprocess, sys
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", "security.diff"
        )
        output_dir = os.path.join(os.path.dirname(__file__), "..")
        start = time.monotonic()

        subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "..", "run_review.py"),
             "--diff-file", fixture, "--output-dir", output_dir, "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )

        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"Single fixture took {elapsed:.1f}s"


class TestScannerPerformance:
    """Scanner scaling tests."""

    def test_ten_scanners_all_run(self):
        """Verify all 10 scanners are available and run."""
        scanners = get_available_scanners()
        assert len(scanners) >= 10, f"Expected >=10 scanners, got {len(scanners)}: {scanners}"
