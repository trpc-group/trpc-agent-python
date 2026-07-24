#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI entry point for the deterministic code-review pipeline (issue #92).

Needs no API key — the scanner pipeline produces a full report and persists it. The default runtime
is the sandbox (`auto` → container if Docker is up, else the local subprocess sandbox). For the
LLM-agent path with a fake model, use run_agent.py --dry-run instead. Examples:

    python run_review.py --diff-file my.diff --out-dir /tmp/cr
    python run_review.py --repo-path /path/to/repo
    python run_review.py --files pipeline/engine.py,pipeline/scanners.py
    python run_review.py --fixture security.diff --no-db --runtime inprocess
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

from pipeline import report as report_mod
from pipeline.engine import ReviewResult, run_review, run_review_container

HERE = Path(__file__).parent


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _resolve_runtime(runtime: str) -> str:
    """`auto` -> container when Docker is up (production default), else the local subprocess sandbox."""
    if runtime != "auto":
        return runtime
    return "container" if _docker_available() else "local"


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Automated code-review agent (Skills + sandbox + DB).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--diff-file", help="path to a unified-diff file")
    src.add_argument("--repo-path", help="path to a git worktree (reviews `git diff`)")
    src.add_argument("--files", help="comma-separated list of file paths to review as fully-added")
    src.add_argument("--fixture", help="name of a bundled fixture under fixtures/diffs/")
    ap.add_argument("--runtime",
                    choices=["auto", "inprocess", "local", "container"],
                    default="auto",
                    help="scanner runtime: auto (sandbox: container if Docker, else local), "
                    "inprocess (fast dev), local (subprocess sandbox), container (Docker)")
    ap.add_argument("--sandbox-timeout", type=float, default=None, help="sandbox timeout in seconds")
    ap.add_argument("--out-dir", default=".", help="where to write review_report.json/.md")
    ap.add_argument("--db-url", default="sqlite+aiosqlite:///./code_review.db")
    ap.add_argument("--no-db", action="store_true", help="skip persistence (report files only)")
    return ap.parse_args()


def _run(args: argparse.Namespace) -> ReviewResult:
    runtime = _resolve_runtime(args.runtime)
    if args.repo_path:
        src = {"repo_path": args.repo_path}
    elif args.files:
        src = {"files": [p.strip() for p in args.files.split(",") if p.strip()]}
    else:
        path = Path(args.diff_file) if args.diff_file else HERE / "fixtures" / "diffs" / args.fixture
        src = {"diff_text": path.read_text(encoding="utf-8")}
    # Every input mode reaches the resolved runtime — container goes to the async container path so
    # --files / --repo-path are not silently downgraded to in-process.
    if runtime == "container":
        return asyncio.run(run_review_container(sandbox_timeout=args.sandbox_timeout, **src))
    return run_review(runtime=runtime, sandbox_timeout=args.sandbox_timeout, **src)


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
