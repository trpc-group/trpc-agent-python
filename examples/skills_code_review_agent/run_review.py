#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI entry point for the automated code-review agent (issue #92).

Dry-run / fake-model mode (default) needs no API key: the deterministic scanner pipeline produces a
full report and persists it. Examples:

    python run_review.py --diff-file fixtures/diffs/0001_insecure.diff --out-dir /tmp/cr
    python run_review.py --repo-path /path/to/repo
    python run_review.py --fixture 0006_eval.diff --no-db
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pipeline import report as report_mod
from pipeline.engine import ReviewResult, run_review, run_review_container

HERE = Path(__file__).parent


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Automated code-review agent (Skills + sandbox + DB).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--diff-file", help="path to a unified-diff file")
    src.add_argument("--repo-path", help="path to a git worktree (reviews `git diff`)")
    src.add_argument("--fixture", help="name of a bundled fixture under fixtures/diffs/")
    ap.add_argument("--runtime",
                    choices=["inprocess", "local", "container"],
                    default="inprocess",
                    help="scanner runtime: inprocess (fast), local (subprocess sandbox), container (Docker)")
    ap.add_argument("--sandbox-timeout", type=float, default=None, help="sandbox timeout in seconds")
    ap.add_argument("--out-dir", default=".", help="where to write review_report.json/.md")
    ap.add_argument("--db-url", default="sqlite+aiosqlite:///./code_review.db")
    ap.add_argument("--no-db", action="store_true", help="skip persistence (report files only)")
    return ap.parse_args()


def _run(args: argparse.Namespace) -> ReviewResult:
    if args.repo_path:
        return run_review(repo_path=args.repo_path, runtime=args.runtime, sandbox_timeout=args.sandbox_timeout)
    path = Path(args.diff_file) if args.diff_file else HERE / "fixtures" / "diffs" / args.fixture
    diff_text = path.read_text(encoding="utf-8")
    if args.runtime == "container":
        return asyncio.run(run_review_container(diff_text=diff_text, sandbox_timeout=args.sandbox_timeout))
    return run_review(diff_text=diff_text, runtime=args.runtime, sandbox_timeout=args.sandbox_timeout)


async def _persist(result: ReviewResult, db_url: str) -> None:
    from storage.dao import ReviewStore  # imported lazily so --no-db needs no DB deps

    store = ReviewStore(db_url)
    await store.init()
    try:
        await store.persist(result)
    finally:
        await store.close()


def main() -> None:
    args = _parse_args()
    result = _run(args)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review_report.json").write_text(report_mod.render_json(result.report), encoding="utf-8")
    (out / "review_report.md").write_text(report_mod.render_md(result.report), encoding="utf-8")

    if not args.no_db:
        asyncio.run(_persist(result, args.db_url))

    s = result.report.findings_summary
    print(f"[{result.task_id}] {s.get('total', 0)} findings "
          f"({s.get('warnings', 0)} warnings, {s.get('needs_human_review', 0)} for human review) "
          f"-> {out}/review_report.json"
          f"{'' if args.no_db else '  | persisted: task ' + result.task_id}")


if __name__ == "__main__":
    main()
