#!/usr/bin/env python3
"""CLI entrypoint for the skills code review agent example."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from agent.review_engine import ReviewConfig, run_review
from agent.storage import ReviewStore
from agent.telemetry import configure_console_exporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the skills-based code review agent prototype.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--diff-file", type=Path, help="Path to a unified diff or PR patch file.")
    source.add_argument("--repo-path", type=Path, help="Git repository path; uses git diff for local changes.")
    source.add_argument("--fixture", help="Fixture name under fixtures/, without .diff.")
    parser.add_argument("--path-list-file", type=Path, help="File containing paths to diff within --repo-path.")
    parser.add_argument("--output-dir", type=Path, default=Path("out"), help="Directory for review_report.json/md.")
    parser.add_argument("--db-path", type=Path, default=Path("review_agent.sqlite3"), help="SQLite database path.")
    parser.add_argument("--runtime", choices=["container", "cube", "local", "dry-run-local"], default="container")
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic local fallback without model API calls.")
    parser.add_argument("--fake-model", action="store_true", help="Alias for deterministic fake-model mode.")
    parser.add_argument("--allow-local-fallback", action="store_true", help="Allow local fallback when container is unavailable.")
    parser.add_argument("--task-id", help="Optional stable review task id.")
    parser.add_argument("--timeout-seconds", type=float, default=10.0, help="Per sandbox command timeout.")
    parser.add_argument("--max-output-bytes", type=int, default=65536, help="Per sandbox command output cap.")
    parser.add_argument(
        "--demo-filter-intercept",
        action="store_true",
        help="Add an explicit curl-pipe-shell probe to demonstrate a recorded Filter intercept.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace an existing task bundle with the same --task-id.",
    )
    parser.add_argument(
        "--telemetry-console",
        action="store_true",
        help="Export code_review.* OpenTelemetry spans to stdout.",
    )
    parser.add_argument("--query-task-id", help="Read a persisted task bundle by task id and exit.")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments and enforce relationships argparse cannot express."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.path_list_file and not args.repo_path:
        parser.error("--path-list-file requires --repo-path")
    if not args.query_task_id and not any((args.diff_file, args.repo_path, args.fixture)):
        parser.error("one of --diff-file, --repo-path or --fixture is required")
    return args


def main() -> None:
    args = parse_args()
    if args.query_task_id:
        store = ReviewStore(args.db_path)
        try:
            print(json.dumps(store.get_task(args.query_task_id), ensure_ascii=False, indent=2, sort_keys=True))
        finally:
            store.close()
        return

    if args.telemetry_console:
        configure_console_exporter()

    result = run_review(
        ReviewConfig(
            diff_file=args.diff_file,
            repo_path=args.repo_path,
            path_list_file=args.path_list_file,
            fixture=args.fixture,
            output_dir=args.output_dir,
            db_path=args.db_path,
            runtime=args.runtime,
            dry_run=args.dry_run,
            fake_model=args.fake_model,
            allow_local_fallback=args.allow_local_fallback,
            task_id=args.task_id,
            timeout_seconds=args.timeout_seconds,
            max_output_bytes=args.max_output_bytes,
            include_high_risk_probe=args.demo_filter_intercept,
            overwrite_task=args.overwrite,
        )
    )
    print(
        json.dumps(
            {
                "task_id": result.task_id,
                "review_report_json": str(result.report_json_path),
                "review_report_md": str(result.report_md_path),
                "db_path": str(result.db_path),
                "summary": result.report["summary"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
