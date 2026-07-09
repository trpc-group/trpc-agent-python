#!/usr/bin/env python3
"""Code Review Agent — main entry point.

Usage:
    python run_review.py --diff-file fixtures/diffs/security.diff
    python run_review.py --diff-file fixtures/diffs/security.diff --dry-run
    python run_review.py --repo-path /path/to/repo
"""

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.config import ReviewConfig, load_config
from pipeline.diff_parser import parse_diff, summarize_diff
from pipeline.filter_chain import FilterChain
from pipeline.sandbox import execute_in_sandbox
from pipeline.scanners import run_scanners, get_available_scanners
from pipeline.dedup import deduplicate, separate_low_confidence
from pipeline.redaction import redact_finding_evidence
from pipeline.report import (
    build_recommendations,
    generate_json_report,
    generate_md_report,
)
from pipeline.telemetry import TelemetryCollector
from pipeline.types import ReviewReport, SandboxRun
from storage.dao import ReviewDatabase
from storage.models import (
    FilterLogRecord,
    FindingRecord,
    ReviewTaskRecord,
    SandboxRunRecord,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automated Code Review Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_review.py --diff-file fixtures/diffs/security.diff
  python run_review.py --diff-file fixtures/diffs/security.diff --dry-run
  python run_review.py --repo-path .
        """,
    )
    parser.add_argument("--diff-file", help="Path to a unified diff file")
    parser.add_argument("--repo-path", help="Path to a git repository")
    parser.add_argument("--output-dir", default=".", help="Output directory")
    parser.add_argument("--db-path", default="review_history.db", help="SQLite DB path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without real model calls")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    args = parser.parse_args()

    if not args.diff_file and not args.repo_path:
        parser.error("Either --diff-file or --repo-path is required")

    # Load config
    cfg = load_config(
        diff_file=args.diff_file,
        repo_path=args.repo_path,
        output_dir=args.output_dir,
        db_path=args.db_path,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    # Generate task ID
    task_id = f"review-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # Telemetry
    tel = TelemetryCollector()

    # ── Stage 1: Read diff ─────────────────────────────────────
    print(f"[1/8] Reading diff...")
    if cfg.diff_file:
        if not os.path.exists(cfg.diff_file):
            print(f"Error: diff file not found: {cfg.diff_file}", file=sys.stderr)
            return 1
        with open(cfg.diff_file, "r", encoding="utf-8") as f:
            diff_text = f.read()
        diff_source = cfg.diff_file
    elif cfg.repo_path:
        import subprocess
        result = subprocess.run(
            ["git", "-C", cfg.repo_path, "diff", "HEAD"],
            capture_output=True, text=True,
        )
        diff_text = result.stdout
        diff_source = f"git diff @ {cfg.repo_path}"
    else:
        diff_text = ""
        diff_source = "none"

    if cfg.verbose:
        print(f"  Source: {diff_source}")
        print(f"  Size: {len(diff_text)} bytes")

    # ── Stage 2: Parse diff ────────────────────────────────────
    print(f"[2/8] Parsing diff...")
    t0 = time.monotonic()
    files = parse_diff(diff_text)
    tel.parse_duration_ms = int((time.monotonic() - t0) * 1000)
    tel.files_scanned = len(files)

    if not files:
        print("  No changes detected.")
        return 0

    diff_summary = summarize_diff(files)
    print(f"  {diff_summary}")

    # ── Stage 3: Filter chain ──────────────────────────────────
    print(f"[3/8] Running safety filter chain...")
    t0 = time.monotonic()
    filter_chain = FilterChain(extra_patterns=cfg.denied_patterns)
    filter_decision = filter_chain.evaluate(diff_text)
    tel.filter_duration_ms = int((time.monotonic() - t0) * 1000)

    if filter_decision.action == "deny":
        tel.filter_intercepts = 1
        print(f"  [DENIED] {filter_decision.reason}")

        # Save filter log to DB
        db = ReviewDatabase(cfg.db_path)
        db.connect()
        db.insert_task(ReviewTaskRecord(
            task_id=task_id,
            diff_source=diff_source,
            diff_summary=diff_summary,
            status="filtered",
            filter_intercepts=1,
            duration_ms=tel.snapshot()["total_duration_ms"],
        ))
        db.insert_filter_log(FilterLogRecord(
            task_id=task_id,
            action=filter_decision.action,
            reason=filter_decision.reason,
            filter_name=filter_decision.filter_name,
        ))
        db.close()
        return 1

    if filter_decision.action == "needs_human_review":
        print(f"  [WARNING] Flagged for human review: {filter_decision.reason}")
        tel.filter_intercepts = 1
    else:
        print(f"  [OK] Safety checks passed ({filter_chain.get_filters_summary()['total_filters']} filters)")

    # ── Stage 4: Scan ──────────────────────────────────────────
    print(f"[4/8] Scanning code with {len(cfg.enabled_scanners)} scanners...")
    t0 = time.monotonic()
    all_findings = []
    for f in files:
        if f.is_binary:
            if cfg.verbose:
                print(f"  Skipping binary file: {f.filename}")
            continue
        file_findings = run_scanners(f, enabled=cfg.enabled_scanners,
                                     min_confidence=cfg.min_confidence)
        all_findings.extend(file_findings)
        if cfg.verbose and file_findings:
            print(f"  {f.filename}: {len(file_findings)} finding(s)")
    tel.scan_duration_ms = int((time.monotonic() - t0) * 1000)
    tel.total_findings_before_dedup = len(all_findings)
    print(f"  Found {len(all_findings)} raw finding(s)")

    # ── Stage 5: Sandbox execution (if needed) ─────────────────
    print(f"[5/8] Running sandbox checks...")
    t0 = time.monotonic()
    sandbox_runs: list[SandboxRun] = []
    for f in files:
        if f.is_binary or not f.filename.endswith(".py"):
            continue
        # Resolve skill script from repo root (skills/ is at repo root, not here)
        _this_dir = os.path.dirname(os.path.abspath(__file__))
        _repo_root = os.path.dirname(os.path.dirname(_this_dir))  # examples/ -> repo root
        script_path = os.path.join(_repo_root, "skills", "code-review", "scripts", "run_checks.py")
        if not os.path.exists(script_path):
            script_path = os.path.join(_this_dir, "skills", "code-review", "scripts", "run_checks.py")
        # Check if any scanner found issues in this file
        file_findings = [x for x in all_findings if x.file == f.filename]
        if file_findings:
            # Run sandboxed check script
            run = execute_in_sandbox(
                command=["python", script_path, f.filename],
                timeout_seconds=cfg.sandbox_timeout_seconds,
                max_output_bytes=cfg.sandbox_max_output_bytes,
                env_allowlist=cfg.sandbox_env_allowlist,
            )
            sandbox_runs.append(run)
            tel.sandbox_runs += 1
            if run.timed_out or run.exit_code != 0:
                tel.sandbox_failures += 1
    tel.sandbox_total_duration_ms = int((time.monotonic() - t0) * 1000)
    print(f"  {len(sandbox_runs)} sandbox run(s), {tel.sandbox_failures} failure(s)")

    # ── Stage 6: Dedup + redact ────────────────────────────────
    print(f"[6/8] Deduplicating and redacting...")
    t0 = time.monotonic()
    deduped = deduplicate(all_findings)
    tel.total_findings_after_dedup = len(deduped)
    tel.dedup_duration_ms = int((time.monotonic() - t0) * 1000)

    findings_redacted, redact_count = redact_finding_evidence(deduped)
    tel.redaction_count = redact_count
    print(f"  {len(deduped)} finding(s) after dedup, {redact_count} redaction(s)")

    # ── Stage 7: Report ────────────────────────────────────────
    print(f"[7/8] Generating reports...")
    t0 = time.monotonic()
    high_conf, low_conf = separate_low_confidence(deduped)
    sev_counts = _count_severity(deduped)

    report = ReviewReport(
        task_id=task_id,
        findings=deduped,
        filter_summary={
            "decision": filter_decision.action,
            "reason": filter_decision.reason,
            "active_filters": filter_chain.get_filters_summary()["total_filters"],
        },
        sandbox_summary={
            "total_runs": len(sandbox_runs),
            "failures": tel.sandbox_failures,
            "runs": [
                {"command": r.command, "exit_code": r.exit_code,
                 "duration_ms": r.duration_ms, "timed_out": r.timed_out}
                for r in sandbox_runs
            ],
        },
        telemetry=tel.snapshot(),
        human_review_items=low_conf,
        recommendations=build_recommendations(deduped),
    )

    json_report = generate_json_report(report)
    md_report = generate_md_report(report)
    tel.report_duration_ms = int((time.monotonic() - t0) * 1000)

    # Write reports
    json_path = os.path.join(cfg.output_dir, "review_report.json")
    md_path = os.path.join(cfg.output_dir, "review_report.md")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    print(f"  Reports written to {json_path}, {md_path}")

    # ── Stage 8: Persist to DB ─────────────────────────────────
    print(f"[8/8] Saving to database...")
    t0 = time.monotonic()
    db = ReviewDatabase(cfg.db_path)
    db.connect()

    # Task
    db.insert_task(ReviewTaskRecord(
        task_id=task_id,
        diff_source=diff_source,
        diff_summary=diff_summary,
        status="completed",
        files_changed=len(files),
        total_findings=len(deduped),
        critical_count=sev_counts.get("critical", 0),
        high_count=sev_counts.get("high", 0),
        medium_count=sev_counts.get("medium", 0),
        low_count=sev_counts.get("low", 0),
        info_count=sev_counts.get("info", 0),
        sandbox_runs=len(sandbox_runs),
        filter_intercepts=tel.filter_intercepts,
        duration_ms=tel.snapshot()["total_duration_ms"],
    ))

    # Findings
    finding_records = [
        FindingRecord(
            task_id=task_id,
            severity=f.severity.value,
            category=f.category.value,
            file=f.file,
            line=f.line,
            title=f.title,
            evidence=f.evidence,
            recommendation=f.recommendation,
            confidence=f.confidence,
            source=f.source,
        )
        for f in deduped
    ]
    db.insert_findings_batch(finding_records)

    # Sandbox runs
    for run in sandbox_runs:
        db.insert_sandbox_run(SandboxRunRecord(
            task_id=task_id,
            command=run.command,
            exit_code=run.exit_code,
            stdout=run.stdout,
            stderr=run.stderr,
            duration_ms=run.duration_ms,
            timed_out=run.timed_out,
            output_truncated=run.output_truncated,
            error=run.error,
        ))

    # Filter log (if intercepted)
    if filter_decision.action != "allow":
        db.insert_filter_log(FilterLogRecord(
            task_id=task_id,
            action=filter_decision.action,
            reason=filter_decision.reason,
            filter_name=filter_decision.filter_name,
        ))

    tel.db_write_duration_ms = int((time.monotonic() - t0) * 1000)
    db.close()

    # ── Final summary ──────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"Review Complete: {task_id}")
    print(f"{'='*50}")
    print(f"Files changed:  {len(files)}")
    print(f"Findings:       {len(deduped)} (critical: {sev_counts.get('critical', 0)}, "
          f"high: {sev_counts.get('high', 0)}, medium: {sev_counts.get('medium', 0)}, "
          f"low: {sev_counts.get('low', 0)})")
    print(f"Needs review:   {len(low_conf)}")
    print(f"Filter:         {filter_decision.action}")
    print(f"Duration:       {tel.snapshot()['total_duration_ms']}ms")
    print(f"DB:             {cfg.db_path}")

    return 0


def _count_severity(findings) -> dict:
    """Count findings by severity level."""
    counts = {}
    for f in findings:
        key = f.severity.value
        counts[key] = counts.get(key, 0) + 1
    return counts


if __name__ == "__main__":
    sys.exit(main())
