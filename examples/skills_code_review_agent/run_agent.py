#!/usr/bin/env python3
#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""CLI entry point for the Skills-based code review agent."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from agent.workflow import CodeReviewAgent
from agent.workflow import ReviewConfig

EXAMPLE_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = EXAMPLE_ROOT / "tests" / "fixtures"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Review a diff through Skill + Filter + sandbox + SQL storage.",
    )
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--diff-file", type=Path, help="Unified diff or PR patch file.")
    inputs.add_argument("--repo-path", type=Path, help="Git working tree to review.")
    inputs.add_argument("--files", type=Path, nargs="+", help="File paths treated as new-file changes.")
    inputs.add_argument("--fixture", help="Fixture name under tests/fixtures.")
    inputs.add_argument("--run-all-fixtures", action="store_true", help="Run every public diff fixture.")

    parser.add_argument(
        "--runtime",
        choices=("container", "local"),
        default="container",
        help="Container is the production default; local is a development fallback.",
    )
    parser.add_argument(
        "--db-url",
        default=f"sqlite:///{EXAMPLE_ROOT / 'data' / 'reviews.db'}",
        help="SQLAlchemy URL. SQLite is the default; other SQL backends remain supported.",
    )
    parser.add_argument("--output-dir", type=Path, default=EXAMPLE_ROOT / "output")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--max-output-bytes", type=int, default=64 * 1024)
    parser.add_argument("--checker-script", default="scripts/review_diff.py")
    parser.add_argument(
        "--network-host",
        action="append",
        default=[],
        help="Requested network host. The default policy denies all hosts.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full deterministic pipeline without any model API call.",
    )
    parser.add_argument(
        "--fake-model",
        action="store_true",
        help="Record fake-model mode; parsing, sandbox, storage, and reports still execute.",
    )
    return parser


def make_config(args: argparse.Namespace, output_dir: Path | None = None) -> ReviewConfig:
    return ReviewConfig(
        runtime=args.runtime,
        db_url=args.db_url,
        output_dir=output_dir or args.output_dir,
        skill_root=EXAMPLE_ROOT / "skills",
        work_root=EXAMPLE_ROOT / "data" / "workspaces",
        checker_script=args.checker_script,
        timeout_seconds=args.timeout,
        max_output_bytes=args.max_output_bytes,
        network_hosts=args.network_host,
        dry_run=args.dry_run,
        fake_model=args.fake_model,
    )


async def run_one(args: argparse.Namespace, fixture: str | None = None):
    output_dir = args.output_dir / Path(fixture).stem if fixture else args.output_dir
    agent = CodeReviewAgent(make_config(args, output_dir))
    if fixture:
        report = await agent.review(fixture=FIXTURE_ROOT / fixture)
    elif args.diff_file:
        report = await agent.review(diff_file=args.diff_file)
    elif args.repo_path:
        report = await agent.review(repo_path=args.repo_path)
    elif args.files:
        report = await agent.review(files=args.files)
    else:
        report = await agent.review(fixture=FIXTURE_ROOT / args.fixture)
    print_summary(report, args.db_url, output_dir)
    return report


async def main_async(args: argparse.Namespace) -> int:
    if args.run_all_fixtures:
        fixtures = sorted(path.name for path in FIXTURE_ROOT.glob("*.diff"))
        reports = [await run_one(args, fixture) for fixture in fixtures]
        print("=" * 72)
        print(f"PUBLIC FIXTURE RESULT: {len(reports)}/{len(fixtures)} reports generated")
        print(f"TOTAL FINDINGS: {sum(len(item.findings) for item in reports)}")
        print(f"TOTAL FILTER INTERCEPTIONS: {sum(item.monitoring.interception_count for item in reports)}")
        return 0
    await run_one(args)
    return 0


def print_summary(report, db_url: str, output_dir: Path) -> None:
    print("=" * 72)
    print("tRPC-Agent Skills Code Review")
    print("=" * 72)
    print(f"TASK ID            {report.task_id}")
    print(f"STATUS             {report.status}")
    print(f"CONCLUSION         {report.conclusion}")
    print(f"INPUT              {report.input_summary.file_count} files, "
          f"{report.input_summary.changed_line_count} changed lines")
    decision = report.filter_decisions[0]
    print(f"FILTER             {decision.decision.upper()} ({decision.rule_id})")
    if report.sandbox_runs:
        run = report.sandbox_runs[0]
        print(f"SANDBOX            {run.runtime} / {run.status} / {run.duration_ms} ms")
        print(f"SKILL FLOW         skill_load={run.skill_loaded} skill_run=True")
    else:
        print("SANDBOX            not started")
        print("SKILL FLOW         skill_run=False")
    print(f"FINDINGS           {len(report.findings)} high confidence")
    print(f"HUMAN REVIEW       {len(report.warnings)} low confidence")
    print(f"SEVERITY           {report.monitoring.severity_distribution}")
    print(f"TOOL CALLS         {report.monitoring.tool_calls}")
    print(f"REDACTIONS         {report.monitoring.redaction_count}")
    print(f"TOTAL DURATION     {report.monitoring.total_duration_ms} ms")
    print(f"DATABASE           {db_url}")
    print(f"JSON REPORT        {output_dir / 'review_report.json'}")
    print(f"MARKDOWN REPORT    {output_dir / 'review_report.md'}")


def main() -> int:
    args = build_parser().parse_args()
    if args.fixture and not (FIXTURE_ROOT / args.fixture).is_file():
        raise SystemExit(f"fixture not found: {args.fixture}")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
