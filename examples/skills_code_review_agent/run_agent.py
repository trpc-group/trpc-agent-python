#!/usr/bin/env python3
"""Deterministic dry-run code review example.

This entrypoint intentionally avoids LLM calls and remote execution. It is a
small, reproducible loop for issue #92 phase 1:

unified diff -> changed lines -> static rules -> redacted JSON/Markdown report.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Sequence

from agent.diff_parser import parse_unified_diff
from agent.filtering import evaluate_filter_decision
from agent.report import build_report
from agent.report import write_reports
from agent.rules import run_static_rules
from agent.sandbox import DryRunSandboxRunner
from agent.storage import persist_review
from agent.telemetry import build_telemetry_summary


def _resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def _default_diff_path() -> str:
    return str(Path(__file__).resolve().parent / "fixtures" / "security.diff")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic code review over a unified diff.")
    parser.add_argument(
        "--diff-file",
        default=_default_diff_path(),
        help="Path to a unified diff file. Defaults to fixtures/security.diff.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "output"),
        help="Directory where review_report.json and review_report.md will be written.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Record the run as dry-run. No external services are called either way.",
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="Optional SQLite database path for persisting review tasks, findings, and reports.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    started = time.perf_counter()
    args = parse_args(argv)
    diff_file = _resolve_path(args.diff_file)
    output_dir = _resolve_path(args.output_dir)
    db_path = _resolve_path(args.db_path) if args.db_path else None

    if not diff_file.exists():
        raise FileNotFoundError(f"diff file not found: {diff_file}")

    diff_text = diff_file.read_text(encoding="utf-8")
    parsed_diff = parse_unified_diff(diff_text)
    filter_decision = evaluate_filter_decision(diff_text, parsed_diff)
    findings = run_static_rules(parsed_diff.changed_lines)
    sandbox_run = DryRunSandboxRunner().run(
        files=parsed_diff.files,
        findings=findings,
        filter_decision=filter_decision,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    telemetry_summary = build_telemetry_summary(
        files_scanned=parsed_diff.files,
        findings=findings,
        sandbox_run=sandbox_run,
        filter_decision=filter_decision,
        duration_ms=duration_ms,
    )
    report = build_report(
        diff_file=args.diff_file,
        files=parsed_diff.files,
        findings=findings,
        dry_run=args.dry_run,
        filter_summary=filter_decision.to_dict(),
        sandbox_summary=sandbox_run.to_dict(),
        telemetry_summary=telemetry_summary,
    )
    json_path, md_path = write_reports(report, output_dir)
    task_id = ""
    if db_path is not None:
        task_id = persist_review(
            db_path=db_path,
            report=report,
            json_report_path=json_path,
            markdown_report_path=md_path,
        )

    high_count = report["summary"]["severity_counts"].get("high", 0)
    medium_count = report["summary"]["severity_counts"].get("medium", 0)
    low_count = report["summary"]["severity_counts"].get("low", 0)

    print("Skills code review dry-run complete")
    print(f"Diff file: {diff_file}")
    print(f"Changed files: {len(parsed_diff.files)}")
    print(f"Findings: high={high_count} medium={medium_count} low={low_count}")
    print(f"Filter decision: {filter_decision.decision} ({filter_decision.reason})")
    print(f"Sandbox status: {sandbox_run.status}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    if db_path is not None:
        print(f"Database: {db_path}")
        print(f"Task ID: {task_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
