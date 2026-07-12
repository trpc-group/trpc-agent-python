# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the code review dry-run example."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.inputs import load_review_input
from agent.pipeline import ReviewRunConfig
from agent.pipeline import run_review
from agent.report import render_markdown_report
from agent.report import report_to_json
from agent.report import write_report_files
from agent.schemas import SandboxPolicy
from agent.storage import ReviewStorage


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Run the code review Agent dry-run prototype.")
    parser.add_argument("--diff-file", type=Path, help="Path to a unified diff file.")
    parser.add_argument("--repo-path", type=Path, help="Path to a local git repository whose diff should be reviewed.")
    parser.add_argument("--base-ref", help="Optional base ref for repo-path mode, e.g. origin/main.")
    parser.add_argument("--fake-model", action="store_true", default=True, help="Use deterministic fake-model mode.")
    parser.add_argument("--sandbox-runtime", default="fake", choices=("fake", "container"), help="Sandbox runtime.")
    parser.add_argument("--container-image", default="python:3-slim", help="Docker image for --sandbox-runtime container.")
    parser.add_argument("--sandbox-timeout-seconds", type=int, default=10, help="Sandbox timeout budget.")
    parser.add_argument("--max-output-bytes", type=int, default=4096, help="Maximum sandbox output bytes to retain.")
    parser.add_argument("--db-path", type=Path, help="SQLite database path for persisted review results.")
    parser.add_argument("--show-task", help="Show a persisted task by id instead of running a review.")
    parser.add_argument("--output-dir", type=Path, help="Directory for review_report.json and review_report.md.")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout.")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown report to stdout.")
    parser.add_argument("--fail-on-findings", action="store_true", help="Exit 1 when high-confidence findings exist.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)

    if args.show_task:
        if not args.db_path:
            print("--show-task requires --db-path", file=sys.stderr)
            return 2
        storage = ReviewStorage(args.db_path)
        try:
            task = storage.get_task(args.show_task)
        finally:
            storage.close()
        if task is None:
            print(f"task not found: {args.show_task}", file=sys.stderr)
            return 3
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return 0

    if bool(args.diff_file) == bool(args.repo_path):
        print("provide exactly one of --diff-file or --repo-path", file=sys.stderr)
        return 2

    try:
        bundle = load_review_input(diff_file=args.diff_file, repo_path=args.repo_path, base_ref=args.base_ref)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    policy = SandboxPolicy(
        runtime=args.sandbox_runtime,
        timeout_seconds=args.sandbox_timeout_seconds,
        max_output_bytes=args.max_output_bytes,
        network_allowed=False,
    )
    report = run_review(
        bundle.diff_text,
        parsed_diff=bundle.parsed_diff,
        review_input=bundle.review_input,
        config=ReviewRunConfig(
            fake_model=args.fake_model,
            sandbox_policy=policy,
            db_path=args.db_path,
            container_image=args.container_image,
        ),
    )

    printed = False
    if args.json:
        print(report_to_json(report))
        printed = True
    if args.markdown:
        print(render_markdown_report(report), end="")
        printed = True

    if args.output_dir:
        json_path, markdown_path = write_report_files(report, args.output_dir)
        if not printed:
            print(f"Wrote {json_path}")
            print(f"Wrote {markdown_path}")
            printed = True

    if not printed:
        print(render_markdown_report(report), end="")

    if args.fail_on_findings and report.findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
