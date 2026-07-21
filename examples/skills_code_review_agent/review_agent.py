# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Main entry point for the code review agent.

Integrates all modules into a complete review pipeline:
Input parsing → Filter governance → Skill rules → Sandbox execution
→ Finding dedup & classification → Report generation → DB persistence
"""

from __future__ import annotations

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
from .filter_chain import ReviewFilterChain, create_review_filter_chain
from .models import (
    FilterIntercept,
    Finding,
    ReviewReport,
    ReviewStatus,
    ReviewTask,
    SandboxRun,
    Severity,
    FindingCategory,
    Confidence,
)
from .monitor import ReviewMonitor
from .report_generator import write_reports
from .sandbox import create_sandbox
from .secret_masker import mask_report


def run_review(config: ReviewAgentConfig) -> Optional[ReviewReport]:
    """Execute a complete code review pipeline.

    Args:
        config: The review agent configuration.

    Returns:
        ReviewReport if the review completed, or None on fatal error.
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

    # ── Initialize monitor ──
    monitor = ReviewMonitor(storage, task_id)
    monitor.start()

    try:
        # ════════════════════════════════════════
        # ① Input parsing
        # ════════════════════════════════════════
        parse_start = time.monotonic()
        diff_result = load_input(
            diff_file=config.input_value if config.input_source == "diff_file" else None,
            repo_path=config.input_value if config.input_source == "repo_path" else None,
            fixture=config.input_value if config.input_source == "fixture" else None,
        )
        parse_duration = (time.monotonic() - parse_start) * 1000
        monitor.record_parse_duration(parse_duration)

        # Update task with input summary
        file_count = len(diff_result.files)
        storage.update_task_status(task_id, ReviewStatus.RUNNING)

        # ════════════════════════════════════════
        # ② Filter governance
        # ════════════════════════════════════════
        filter_start = time.monotonic()
        filter_chain = create_review_filter_chain(storage, task_id)

        # Evaluate each file's diff content against filters
        for changed_file in diff_result.files:
            for hunk in changed_file.hunks:
                hunk_text = "\n".join(hunk.lines)
                filter_result = filter_chain.evaluate(hunk_text)
                for intercept in filter_result.intercepts:
                    monitor.record_intercept()

                if filter_result.final_action.value == "deny":
                    # Create a finding for the blocked content
                    finding = Finding(
                        task_id=task_id,
                        severity=Severity.CRITICAL,
                        category=FindingCategory.SECURITY,
                        file=changed_file.new_path,
                        line=hunk.new_start,
                        title=f"Filter denied: {intercept.rule}",
                        evidence=hunk_text[:200],
                        recommendation=f"Remove the flagged content: {intercept.reason}",
                        confidence=Confidence.HIGH,
                        source="filter",
                    )
                    storage.add_finding(finding)

        filter_duration = (time.monotonic() - filter_start) * 1000
        monitor.record_filter_duration(filter_duration)

        # ════════════════════════════════════════
        # ③ Sandbox execution (skip in dry-run)
        # ════════════════════════════════════════
        sandbox_runs: list[SandboxRun] = []
        if not config.dry_run and not config.fake_model:
            sandbox = create_sandbox(
                sandbox_type=config.sandbox_type,
                timeout=config.sandbox_timeout,
                max_output_bytes=config.sandbox_max_output,
            )

            # Run security check script on each changed file
            for changed_file in diff_result.files[:5]:  # Limit to 5 files
                file_path = changed_file.new_path or changed_file.old_path
                if file_path == "/dev/null":
                    continue

                sandbox_start = time.monotonic()
                command = f"python3 scripts/check_security.py --file {file_path}"
                sb_result = sandbox.execute(command, cwd=config.output_dir)
                sandbox_duration = (time.monotonic() - sandbox_start) * 1000
                monitor.record_sandbox_duration(sandbox_duration)

                sandbox_run = SandboxRun(
                    task_id=task_id,
                    script_name="check_security.py",
                    runtime=config.sandbox_type,
                    duration_ms=sandbox_duration,
                    exit_code=sb_result.exit_code,
                    output_size_bytes=len(sb_result.stdout.encode("utf-8")),
                    output_truncated=sb_result.output_truncated,
                    success=sb_result.success,
                    error_message=sb_result.error_message,
                )
                storage.add_sandbox_run(sandbox_run)
                sandbox_runs.append(sandbox_run)

                # Parse findings from sandbox output
                if sb_result.success and sb_result.stdout:
                    import json
                    try:
                        data = json.loads(sb_result.stdout)
                        for result in data.get("results", []):
                            for finding_data in result.get("findings", []):
                                finding = Finding(
                                    task_id=task_id,
                                    severity=Severity(finding_data.get("severity", "medium")),
                                    category=FindingCategory.SECURITY,
                                    file=result.get("file", file_path),
                                    line=finding_data.get("line", 0),
                                    title=finding_data.get("message", "Security issue"),
                                    evidence=finding_data.get("message", ""),
                                    recommendation=f"Fix the issue: {finding_data.get('rule', 'unknown')}",
                                    confidence=Confidence(
                                        "high" if finding_data.get("severity") in ("critical", "high") else "medium"
                                    ),
                                    source="sandbox",
                                )
                                storage.add_finding(finding)
                    except (json.JSONDecodeError, ValueError):
                        pass

        # ════════════════════════════════════════
        # ④ Dedup & classify findings
        # ════════════════════════════════════════
        all_findings = storage.get_findings(task_id)
        deduper = Deduplicator()
        findings, warnings, needs_review = deduper.process(all_findings)

        # Persist classified findings
        for f in findings + warnings + needs_review:
            storage.add_finding(f)

        # Record finding stats in monitor
        monitor.record_findings(findings, warnings, needs_review)

        # ════════════════════════════════════════
        # ⑤ Build report
        # ════════════════════════════════════════
        # Get all persisted data
        filter_intercepts = storage.get_filter_intercepts(task_id)
        sandbox_runs = storage.get_sandbox_runs(task_id)
        findings = storage.get_findings(task_id)

        # Separate by confidence
        high_conf = [f for f in findings if f.confidence == Confidence.HIGH]
        med_conf = [f for f in findings if f.confidence == Confidence.MEDIUM]
        low_conf = [f for f in findings if f.confidence == Confidence.LOW]

        report = ReviewReport(
            task=task,
            findings=high_conf + med_conf,
            warnings=[f for f in med_conf if f.severity in (Severity.WARNING, Severity.INFO)],
            needs_human_review=low_conf,
            sandbox_runs=sandbox_runs,
            filter_intercepts=filter_intercepts,
            monitor=monitor.metrics.to_monitor_summary(task_id),
        )

        # Mask sensitive data before writing
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

        # ════════════════════════════════════════
        # ⑥ Finalize
        # ════════════════════════════════════════
        monitor.finish()
        storage.update_task_status(task_id, ReviewStatus.COMPLETED)

        print(f"\n✅ Review complete: {task_id}")
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
        print(f"\n❌ Review failed: {error_msg}", file=sys.stderr)
        traceback.print_exc()
        return None


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    config = ReviewAgentConfig.from_args(args)

    # Handle --list-fixtures
    if config.list_fixtures:
        fixtures = list_available_fixtures()
        if fixtures:
            print("Available fixtures:")
            for f in fixtures:
                print(f"  {f}")
        else:
            print("No fixtures found.")
        return

    # Validate input source
    if not config.input_source:
        print("Error: No input source specified. Use --diff-file, --repo-path, or --fixture.",
              file=sys.stderr)
        sys.exit(1)

    # Run review
    report = run_review(config)
    if report is None:
        sys.exit(1)


if __name__ == "__main__":
    main()