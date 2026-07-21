# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Dry-run / fake-model mode for the code review agent.

This mode allows testing the full review pipeline (input parsing, filter
governance, sandbox execution, database persistence, report generation)
without requiring a real LLM API key.

In dry-run mode:
- Sandbox scripts are NOT executed (only parsed).
- No LLM calls are made.
- The filter chain is still evaluated.
- Findings are loaded from fixture files or generated as placeholders.
- Database is written and reports are generated normally.

Usage:
    python -m examples.skills_code_review_agent.dry_run --fixture 01_clean
    python -m examples.skills_code_review_agent.dry_run --fixture 02_security_leak
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

from .cli import parse_args
from .config import ReviewAgentConfig
from .db.init_db import init_db
from .db.storage import SqliteStorage, StorageABC
from .deduper import Deduplicator
from .diff_parser import load_input, list_available_fixtures
from .filter_chain import create_review_filter_chain
from .models import (
    Confidence,
    Finding,
    FindingCategory,
    ReviewReport,
    ReviewStatus,
    ReviewTask,
    SandboxRun,
    Severity,
)
from .monitor import ReviewMonitor
from .report_generator import write_reports
from .secret_masker import mask_report


def generate_placeholder_findings(
    task_id: str,
    fixture_name: str,
) -> list[Finding]:
    """Generate placeholder findings based on fixture name for testing.

    Each fixture type has predefined expected findings that simulate
    what a real LLM-based analysis would produce.
    """
    fixture_map = {
        "01_clean": [],
        "01_clean.py": [],
        "02_security_leak": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.SECURITY,
                file="src/config.py",
                line=10,
                title="Hardcoded API key detected",
                evidence='API_KEY = "sk-abc123def456ghi789jkl"',
                recommendation="Move API key to environment variable: os.getenv('API_KEY')",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "02_security_leak.py": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.SECURITY,
                file="src/config.py",
                line=10,
                title="Hardcoded API key detected",
                evidence='API_KEY = "sk-abc123def456ghi789jkl"',
                recommendation="Move API key to environment variable: os.getenv('API_KEY')",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "03_async_resource_leak": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.RESOURCE_LEAK,
                file="src/fetcher.py",
                line=15,
                title="Unclosed aiohttp ClientSession",
                evidence="session = aiohttp.ClientSession()\nresult = await session.get(url)",
                recommendation="Use async with: async with aiohttp.ClientSession() as session:",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "03_async_resource_leak.py": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.RESOURCE_LEAK,
                file="src/fetcher.py",
                line=15,
                title="Unclosed aiohttp ClientSession",
                evidence="session = aiohttp.ClientSession()\nresult = await session.get(url)",
                recommendation="Use async with: async with aiohttp.ClientSession() as session:",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "04_db_connection_leak": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.DB_TRANSACTION,
                file="src/db.py",
                line=22,
                title="Unclosed database connection",
                evidence="conn = sqlite3.connect('database.db')\ncursor = conn.cursor()",
                recommendation="Use context manager: with sqlite3.connect('database.db') as conn:",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "04_db_connection_leak.py": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.DB_TRANSACTION,
                file="src/db.py",
                line=22,
                title="Unclosed database connection",
                evidence="conn = sqlite3.connect('database.db')\ncursor = conn.cursor()",
                recommendation="Use context manager: with sqlite3.connect('database.db') as conn:",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "05_test_missing": [
            Finding(
                task_id=task_id,
                severity=Severity.MEDIUM,
                category=FindingCategory.TEST_MISSING,
                file="src/calculator.py",
                line=5,
                title="New function without test coverage",
                evidence="def calculate_interest(principal, rate, time):",
                recommendation="Add a unit test: test_calculate_interest in tests/test_calculator.py",
                confidence=Confidence.MEDIUM,
                source="dry_run",
            ),
        ],
        "05_test_missing.py": [
            Finding(
                task_id=task_id,
                severity=Severity.MEDIUM,
                category=FindingCategory.TEST_MISSING,
                file="src/calculator.py",
                line=5,
                title="New function without test coverage",
                evidence="def calculate_interest(principal, rate, time):",
                recommendation="Add a unit test: test_calculate_interest in tests/test_calculator.py",
                confidence=Confidence.MEDIUM,
                source="dry_run",
            ),
        ],
        "06_duplicate_finding": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.SECURITY,
                file="src/auth.py",
                line=8,
                title="Hardcoded password in source code",
                evidence='PASSWORD = "super_secret_123"',
                recommendation="Use environment variable or secrets manager",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "06_duplicate_finding.py": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.SECURITY,
                file="src/auth.py",
                line=8,
                title="Hardcoded password in source code",
                evidence='PASSWORD = "super_secret_123"',
                recommendation="Use environment variable or secrets manager",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "07_sandbox_failure": [],
        "07_sandbox_failure.py": [],
        "08_secret_masking": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.SECRET_LEAK,
                file="src/credentials.py",
                line=5,
                title="Sensitive information exposure",
                evidence='API_KEY = "sk-..."',
                recommendation="Mask sensitive data and use environment variables",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
        "08_secret_masking.py": [
            Finding(
                task_id=task_id,
                severity=Severity.HIGH,
                category=FindingCategory.SECRET_LEAK,
                file="src/credentials.py",
                line=5,
                title="Sensitive information exposure",
                evidence='API_KEY = "sk-..."',
                recommendation="Mask sensitive data and use environment variables",
                confidence=Confidence.HIGH,
                source="dry_run",
            ),
        ],
    }
    return fixture_map.get(fixture_name, [])


def run_dry_review(config: ReviewAgentConfig) -> Optional[ReviewReport]:
    """Execute a dry-run review pipeline.

    In dry-run mode, sandbox scripts are not executed and findings are
    generated from predefined placeholder data. This allows testing the
    full pipeline (parsing → filter → DB → report) without LLM or sandbox.
    """
    # ── Initialize database ──
    init_db(config.db_path)
    storage: StorageABC = SqliteStorage(config.db_path)

    # ── Create review task ──
    task = ReviewTask(
        input_type=config.input_source,
        input_summary=config.input_value,
        input_raw=config.input_value,
    )
    storage.create_task(task)
    task_id = task.id

    # ── Monitor ──
    monitor = ReviewMonitor(storage, task_id)
    monitor.start()

    try:
        # ── Input parsing ──
        diff_result = load_input(
            diff_file=config.input_value if config.input_source == "diff_file" else None,
            repo_path=config.input_value if config.input_source == "repo_path" else None,
            fixture=config.input_value if config.input_source == "fixture" else None,
        )
        storage.update_task_status(task_id, ReviewStatus.RUNNING)

        # ── Filter governance ──
        filter_chain = create_review_filter_chain(storage, task_id)
        for changed_file in diff_result.files:
            for hunk in changed_file.hunks:
                hunk_text = "\n".join(hunk.lines)
                filter_result = filter_chain.evaluate(hunk_text)
                for intercept in filter_result.intercepts:
                    monitor.record_intercept()

        # ── Generate placeholder findings ──
        placeholder_findings = generate_placeholder_findings(
            task_id, config.input_value
        )
        for f in placeholder_findings:
            storage.add_finding(f)

        # ── Dedup & classify ──
        all_findings = storage.get_findings(task_id)
        deduper = Deduplicator()
        findings, warnings, needs_review = deduper.process(all_findings)
        monitor.record_findings(findings, warnings, needs_review)

        # ── Build report ──
        filter_intercepts = storage.get_filter_intercepts(task_id)
        sandbox_runs = storage.get_sandbox_runs(task_id)
        stored_findings = storage.get_findings(task_id)

        high_conf = [f for f in stored_findings if f.confidence == Confidence.HIGH]
        med_conf = [f for f in stored_findings if f.confidence == Confidence.MEDIUM]
        low_conf = [f for f in stored_findings if f.confidence == Confidence.LOW]

        report = ReviewReport(
            task=task,
            findings=high_conf + med_conf,
            warnings=[f for f in med_conf if f.severity in (Severity.WARNING, Severity.INFO)],
            needs_human_review=low_conf,
            sandbox_runs=sandbox_runs,
            filter_intercepts=filter_intercepts,
            monitor=monitor.metrics.to_monitor_summary(task_id),
        )

        # Mask sensitive data
        report_dict = report.model_dump()
        mask_report(report_dict)

        # Write reports
        json_path, md_path = write_reports(
            report,
            output_dir=config.output_dir,
            json_path=config.output_json,
            md_path=config.output_md,
        )
        report.report_path_json = json_path
        report.report_path_md = md_path

        # ── Finalize ──
        monitor.finish()
        storage.update_task_status(task_id, ReviewStatus.COMPLETED)

        duration = monitor.metrics.total_duration_ms
        print(f"\n✅ Dry-run review complete: {task_id} ({duration:.0f}ms)")
        print(f"   Findings: {len(report.findings)}")
        print(f"   Warnings: {len(report.warnings)}")
        print(f"   Needs review: {len(report.needs_human_review)}")
        print(f"   JSON: {json_path}")
        print(f"   MD:   {md_path}")

        return report

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        monitor.record_exception(e)
        monitor.finish()
        storage.update_task_status(task_id, ReviewStatus.FAILED, error_message=error_msg)
        print(f"\n❌ Dry-run failed: {error_msg}", file=sys.stderr)
        traceback.print_exc()
        return None


def main() -> None:
    """Entry point for dry-run mode."""
    import sys
    # Force dry-run mode
    sys.argv = [a for a in sys.argv if a != "--dry-run"]

    args = parse_args()
    config = ReviewAgentConfig.from_args(args)
    config.dry_run = True

    if config.list_fixtures:
        fixtures = list_available_fixtures()
        if fixtures:
            print("Available fixtures:")
            for f in fixtures:
                print(f"  {f}")
        else:
            print("No fixtures found.")
        return

    if not config.input_source:
        print("Error: No input source specified. Use --diff-file, --repo-path, or --fixture.",
              file=sys.stderr)
        sys.exit(1)

    report = run_dry_review(config)
    if report is None:
        sys.exit(1)


if __name__ == "__main__":
    main()