# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI entry point for the skills-based code review agent.

Subcommands:
  review   run a review over --diff-file / --repo-path / --files / --fixture
  show     print everything recorded for one task id
  init-db  create the schema (idempotent) and validate the DSN
  eval     score reports against annotated fixtures (see eval/eval.py)

Examples:
  python3 run_agent.py review --diff-file fixtures/02_sql_injection/input.diff --dry-run
  python3 run_agent.py review --fixture 02 --dry-run --unsafe-local
  python3 run_agent.py show --task-id <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from review_agent.diff_parser import parse_diff_file, parse_file_list, parse_repo_workspace  # noqa: E402
from review_agent.pipeline import ReviewOptions, run_review  # noqa: E402
from review_agent.store import ReviewStore  # noqa: E402

FIXTURES_DIR = BASE_DIR / "fixtures"


def _resolve_fixture(token: str) -> Path:
    """Accept '02', '02_sql_injection' or a full path."""
    direct = Path(token)
    if direct.is_dir():
        return direct
    for entry in sorted(FIXTURES_DIR.iterdir()):
        if entry.is_dir() and (entry.name == token or entry.name.startswith(f"{token}_")):
            return entry
    raise FileNotFoundError(f"fixture not found: {token}")


def _load_fixture_meta(fixture_dir: Path) -> dict:
    expected = fixture_dir / "expected.json"
    if expected.is_file():
        try:
            return (json.loads(expected.read_text(encoding="utf-8")) or {}).get("meta") or {}
        except ValueError:
            return {}
    return {}


async def _cmd_review(args: argparse.Namespace) -> int:
    options = ReviewOptions(
        db_url=args.db,
        output_dir=args.output_dir,
        unsafe_local=args.unsafe_local,
        dry_run=args.dry_run,
        run_timeout=args.timeout,
        docker_image=args.docker_image,
        llm_mode=args.llm_mode,
    )

    if args.fixture:
        fixture_dir = _resolve_fixture(args.fixture)
        meta = _load_fixture_meta(fixture_dir)
        options.run_timeout = int(meta.get("run_timeout", options.run_timeout))
        options.inject_sleep = float(meta.get("inject_sleep", 0))
        repo_dir = fixture_dir / "repo"
        parsed = parse_diff_file(str(fixture_dir / "input.diff"),
                                 repo_path=str(repo_dir) if repo_dir.is_dir() else None)
        parsed.input_type = "fixture"
        parsed.input_ref = fixture_dir.name
    elif args.diff_file:
        parsed = parse_diff_file(args.diff_file, repo_path=args.repo_path)
    elif args.repo_path:
        parsed = parse_repo_workspace(args.repo_path)
    elif args.files:
        parsed = parse_file_list(args.files)
    else:
        print("error: one of --diff-file / --repo-path / --files / --fixture is required", file=sys.stderr)
        return 2

    outcome = await run_review(parsed, options)
    summary = outcome.payload["summary"]
    print(f"task {outcome.task_id}: {outcome.status} | "
          f"{summary['finding_count']} finding(s), {summary['warning_count']} warning(s), "
          f"{summary['needs_human_review_count']} human-review item(s)")
    print(f"report: {outcome.report_json_path}")
    print(f"report: {outcome.report_md_path}")
    return 0 if outcome.status in ("succeeded", "partial") else 1


def _print_section(title: str, rows: list) -> None:
    print(f"\n== {title} ({len(rows)}) ==")
    for row in rows:
        print(f"  {row}")


async def _cmd_show(args: argparse.Namespace) -> int:
    store = ReviewStore(args.db)
    await store.init()
    bundle = await store.load_task_bundle(args.task_id)
    if bundle is None:
        recent = await store.list_tasks()
        print(f"task {args.task_id!r} not found. Recent tasks:")
        for task in recent:
            print(f"  {task.id}  {task.created_at}  {task.status}  {task.input_type}:{task.input_ref}")
        await store.close()
        return 1

    task = bundle["task"]
    print(f"task {task.id}: status={task.status} mode={task.mode} runtime={task.runtime} "
          f"dry_run={task.dry_run} input={task.input_type}:{task.input_ref}")
    if task.error:
        print(f"error: {task.error}")
    _print_section("diff files", [
        f"{row.path} [{row.change_type}] hunks={row.hunk_count} candidates={row.candidate_line_count}"
        f"{' SKIPPED: ' + row.skip_reason if row.skipped else ''}" for row in bundle["diff_files"]
    ])
    _print_section("sandbox runs (execution log summary)", [
        f"{row.started_at} {row.tool} `{row.command}` -> {row.status} exit={row.exit_code} "
        f"{row.duration_ms}ms timed_out={row.timed_out}" for row in bundle["sandbox_runs"]
    ])
    _print_section("filter events", [
        f"{row.created_at} {row.tool_name} {row.decision} rule={row.rule} {row.reason}"
        for row in bundle["filter_events"]
    ])
    _print_section("findings", [
        f"[{row.status}] {row.severity} {row.category} {row.file}:{row.line} {row.title} ({row.rule_id})"
        for row in bundle["findings"]
    ])
    _print_section("metrics", [
        f"total={row.total_ms}ms sandbox={row.sandbox_ms}ms tool_calls={row.tool_calls} "
        f"filter_blocks={row.filter_blocks} findings={row.finding_count} severity={row.severity_dist_json}"
        for row in bundle["metrics"]
    ])
    for report in bundle["reports"]:
        if report.format == "json":
            summary = report.summary_json or {}
            print(f"\n== final conclusion ==\n  findings={summary.get('finding_count')} "
                  f"warnings={summary.get('warning_count')} "
                  f"human_review={summary.get('needs_human_review_count')}")
    await store.close()
    return 0


async def _cmd_init_db(args: argparse.Namespace) -> int:
    store = ReviewStore(args.db)
    await store.init()
    await store.close()
    print(f"schema ready at {args.db}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_agent.py",
                                     description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="run a code review")
    review.add_argument("--diff-file", help="unified diff / patch file")
    review.add_argument("--repo-path", help="git working tree (alone, or with --diff-file for repo mode)")
    review.add_argument("--files", nargs="+", help="explicit file list (treated as added)")
    review.add_argument("--fixture", help="fixture id or name, e.g. 02 or 02_sql_injection")
    review.add_argument("--dry-run", action="store_true", help="scripted FakeModel, no API key needed")
    review.add_argument("--llm-mode",
                        choices=["auto", "agent", "hybrid", "off"],
                        default="auto",
                        help="LLM participation: agent drives tools | hybrid one-shot re-judgement | off")
    review.add_argument("--db", default="sqlite:///review.db", help="SQLAlchemy DSN (default sqlite:///review.db)")
    review.add_argument("--output-dir", default=".", help="where review_report.{json,md} are written")
    review.add_argument("--unsafe-local",
                        action="store_true",
                        help="run checks on the host instead of a container (development only)")
    review.add_argument("--timeout", type=int, default=60, help="sandbox timeout seconds (default 60)")
    review.add_argument("--docker-image", default=None, help="custom sandbox image tag")

    show = sub.add_parser("show", help="query one task from the database")
    show.add_argument("--task-id", required=True)
    show.add_argument("--db", default="sqlite:///review.db")

    init_db = sub.add_parser("init-db", help="create tables and validate the DSN")
    init_db.add_argument("--db", default="sqlite:///review.db")

    evalp = sub.add_parser("eval", help="score annotated fixtures (delegates to eval/eval.py)")
    evalp.add_argument("--samples", default=str(FIXTURES_DIR))
    evalp.add_argument("--db", default="sqlite:///eval.db")
    evalp.add_argument("--unsafe-local", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "review":
        return asyncio.run(_cmd_review(args))
    if args.command == "show":
        return asyncio.run(_cmd_show(args))
    if args.command == "init-db":
        return asyncio.run(_cmd_init_db(args))
    if args.command == "eval":
        from eval.eval import run_eval
        return run_eval(samples_dir=args.samples, db_url=args.db, unsafe_local=args.unsafe_local)
    return 2


if __name__ == "__main__":
    sys.exit(main())
