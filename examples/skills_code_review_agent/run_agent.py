#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Automated code review agent CLI.

Usage:
  python run_agent.py review --fixture security_eval --runtime local --dry-run
  python run_agent.py review --diff-file change.diff --runtime container
  python run_agent.py show --task-id <id>
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.config import is_dry_run  # noqa: E402
from review.diff_input import load_diff  # noqa: E402
from review.pipeline import ReviewOptions, run_review  # noqa: E402
from storage.store import ReviewStore  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automated code review agent")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="run a code review")
    review.add_argument("--diff-file", help="path to a unified diff / PR patch")
    review.add_argument("--repo-path", help="git repo; reviews `git diff HEAD`")
    review.add_argument("--fixture", help="bundled fixture name (e.g. security_eval)")
    review.add_argument("--runtime", default="container",
                        choices=["local", "container", "cube"],
                        help="sandbox runtime (default: container; local is dev-only)")
    review.add_argument("--dry-run", action="store_true",
                        help="use the deterministic fake model (no API key needed)")
    review.add_argument("--db-url", default="sqlite:///code_review.db")
    review.add_argument("--output-dir", default="out")
    review.add_argument("--no-llm", action="store_true", help="skip the LLM step")

    show = sub.add_parser("show", help="print a stored review task by id")
    show.add_argument("--task-id", required=True)
    show.add_argument("--db-url", default="sqlite:///code_review.db")
    return parser


async def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "review":
        diff_text, input_type, input_ref = load_diff(
            diff_file=args.diff_file, repo_path=args.repo_path, fixture=args.fixture)
        dry_run = is_dry_run(args.dry_run)
        if dry_run and not args.dry_run:
            print("no TRPC_AGENT_API_KEY configured; falling back to --dry-run mode")
        result = await run_review(ReviewOptions(
            diff_text=diff_text, input_type=input_type, input_ref=input_ref,
            runtime=args.runtime, dry_run=dry_run, db_url=args.db_url,
            output_dir=args.output_dir, enable_llm=not args.no_llm))
        print(f"task_id={result.task_id}")
        print(f"conclusion={result.report['conclusion']}")
        print(f"report_json={result.json_path}")
        print(f"report_md={result.md_path}")
        return 0

    store = ReviewStore(db_url=args.db_url)
    try:
        bundle = await store.get_task_bundle(args.task_id)
    finally:
        await store.close()
    if bundle["task"] is None:
        print(f"task {args.task_id!r} not found", file=sys.stderr)
        return 1
    print(json.dumps(bundle, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
