# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI entry of the automatic code-review agent example.

Examples::

    # Review a committed fixture, fully offline (fake model, local sandbox):
    python run_agent.py review --fixture security_issue --dry-run

    # Review a diff file / a git working tree / a list of files:
    python run_agent.py review --diff-file my.patch
    python run_agent.py review --repo-path /path/to/repo
    python run_agent.py review --files a.py b.py

    # Query the database afterwards:
    python run_agent.py show --task-id <ID>
    python run_agent.py list
    python run_agent.py init-db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codereview import inputs as review_inputs  # noqa: E402
from codereview.config import ReviewConfig  # noqa: E402
from codereview.config import SandboxConfig  # noqa: E402
from codereview.config import default_db_url  # noqa: E402
from codereview.config import resolve_sandbox_kind  # noqa: E402
from codereview.pipeline import ReviewPipeline  # noqa: E402
from codereview.store import SqlReviewStore  # noqa: E402
from codereview.store.init_db import init_db  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_agent.py",
                                     description="Automatic code-review agent "
                                                 "(Skills + sandbox + DB, trpc_agent_sdk).")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="run one review task")
    source = review.add_mutually_exclusive_group(required=True)
    source.add_argument("--diff-file", help="unified diff / PR patch file")
    source.add_argument("--repo-path", help="git repository working tree")
    source.add_argument("--files", nargs="+", help="explicit file list (treated as additions)")
    source.add_argument("--fixture", help="committed fixture name, e.g. security_issue")
    review.add_argument("--out-dir", default="out", help="report output directory (default: out)")
    review.add_argument("--db-url", default="", help="SQLAlchemy async URL "
                                                     "(default: sqlite under --out-dir)")
    review.add_argument("--sandbox", default="auto",
                        choices=("auto", "local", "container", "cube"),
                        help="sandbox runtime; auto (default) picks container when "
                             "Docker is available and falls back to the local dev "
                             "runtime (with a warning) otherwise")
    review.add_argument("--model-mode", default="fake", choices=("fake", "real", "off"),
                        help="LLM summary mode; fake needs no API key (default)")
    review.add_argument("--dry-run", action="store_true",
                        help="force offline mode: fake model + local sandbox, no API key needed")
    review.add_argument("--timeout", type=float, default=30.0, help="sandbox timeout seconds")
    review.add_argument("--max-output-bytes", type=int, default=64_000,
                        help="sandbox stdout/stderr cap")
    review.add_argument("--inject-sandbox-failure", action="store_true",
                        help="deterministically fail the sandbox check (resilience demo)")

    show = sub.add_parser("show", help="print everything recorded for one task id")
    show.add_argument("--task-id", required=True)
    show.add_argument("--db-url", default="")
    show.add_argument("--out-dir", default="out",
                      help="directory of the default sqlite DB (default: out)")

    list_cmd = sub.add_parser("list", help="list recent review tasks")
    list_cmd.add_argument("--db-url", default="")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--out-dir", default="out",
                          help="directory of the default sqlite DB (default: out)")

    initdb = sub.add_parser("init-db", help="create / migrate the database schema")
    initdb.add_argument("--db-url", default="")
    initdb.add_argument("--out-dir", default="out",
                        help="directory of the default sqlite DB (default: out)")
    return parser


def _resolve_db_url(args) -> str:
    if getattr(args, "db_url", ""):
        return args.db_url
    # Every subcommand shares the same default DB location (out/review.db) so
    # the documented review → show/list workflow works without --db-url.
    base_dir = getattr(args, "out_dir", "") or "out"
    # SQLite does not create missing parent directories itself.
    os.makedirs(base_dir, exist_ok=True)
    return default_db_url(base_dir)


def _resolve_changeset(args) -> review_inputs.RawChangeSet:
    if args.diff_file:
        return review_inputs.from_diff_file(args.diff_file)
    if args.repo_path:
        return review_inputs.from_repo_path(args.repo_path)
    if args.files:
        return review_inputs.from_file_list(args.files)
    return review_inputs.from_fixture(args.fixture)


async def _cmd_review(args) -> int:
    if args.dry_run:
        args.model_mode = "fake"
        args.sandbox = "local"
    args.sandbox = resolve_sandbox_kind(args.sandbox)

    config = ReviewConfig(
        db_url=_resolve_db_url(args),
        out_dir=args.out_dir,
        model_mode=args.model_mode,
        sandbox=SandboxConfig(
            runtime_kind=args.sandbox,
            timeout_sec=args.timeout,
            max_output_bytes=args.max_output_bytes,
            force_fail=args.inject_sandbox_failure,
        ),
    )
    try:
        changeset = _resolve_changeset(args)
    except (FileNotFoundError, ValueError) as ex:
        print(f"input error: {ex}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as ex:
        detail = (ex.stderr or "").strip()
        print(f"input error: git failed: {detail or ex}", file=sys.stderr)
        return 2
    store = SqlReviewStore(config.db_url)
    try:
        await store.initialize()
        result = await ReviewPipeline(store, config).run(changeset)
    finally:
        await store.close()

    metrics = result.metrics
    print(f"task id     : {result.task_id}")
    print(f"status      : {result.status}")
    print(f"findings    : {metrics.finding_count} "
          f"(severity: {json.dumps(metrics.severity_distribution)})")
    print(f"human review: {metrics.needs_human_review_count}   "
          f"deduplicated: {metrics.deduplicated_count}   "
          f"filter blocks: {metrics.filter_block_count}")
    print(f"duration    : {metrics.total_duration_ms:.0f} ms "
          f"(sandbox {metrics.sandbox_duration_ms:.0f} ms, "
          f"{metrics.sandbox_run_count} run(s))")
    print(f"report json : {result.report_paths['json']}")
    print(f"report md   : {result.report_paths['markdown']}")
    print(f"database    : {config.db_url}")
    return 0


async def _cmd_show(args) -> int:
    store = SqlReviewStore(_resolve_db_url(args))
    try:
        await store.initialize()
        bundle = await store.get_task_bundle(args.task_id)
    finally:
        await store.close()
    if bundle["task"] is None:
        print(f"task not found: {args.task_id}", file=sys.stderr)
        return 1
    print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
    return 0


async def _cmd_list(args) -> int:
    store = SqlReviewStore(_resolve_db_url(args))
    try:
        await store.initialize()
        tasks = await store.list_tasks(limit=args.limit)
    finally:
        await store.close()
    if not tasks:
        print("(no review tasks recorded)")
        return 0
    for task in tasks:
        print(f"{task['id']}  {task['created_at']}  {task['status']:<24} "
              f"{task['input_type']}:{task['input_ref']}")
    return 0


async def _cmd_init_db(args) -> int:
    db_url = _resolve_db_url(args)
    tables = await init_db(db_url)
    print(f"database ready at {db_url}")
    for table in tables:
        print(f"  - {table}")
    return 0


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    handler = {
        "review": _cmd_review,
        "show": _cmd_show,
        "list": _cmd_list,
        "init-db": _cmd_init_db,
    }[args.command]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
