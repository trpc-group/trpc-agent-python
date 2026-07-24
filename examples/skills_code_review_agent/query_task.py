#!/usr/bin/env python3
"""ReviewMind 数据库查询工具

按 task_id 查询任务状态、执行日志摘要、Filter 拦截记录、监控摘要、findings 和最终结论。

Usage:
    python query_task.py <task_id>
    python query_task.py <task_id> --db-path /path/to/review.db
    python query_task.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure the package is importable
_parent = Path(__file__).resolve().parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from storage.sqlite_repository import SqliteCrRepository


DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "reports", "review.db")


def query_task(task_id: str, db_path: str) -> None:
    """Query and display a review task by ID.

    Args:
        task_id: The review task ID.
        db_path: Path to the SQLite database.
    """
    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    repo = SqliteCrRepository(db_path)

    task = repo.get_task(task_id)
    if not task:
        print(f"❌ Task not found: {task_id}")
        repo.close()
        sys.exit(1)

    print(f"📋 Review Task: {task.id}")
    print(f"   Status: {task.status.value}")
    print(f"   Input Type: {task.input_type}")
    print(f"   Duration: {task.total_duration_ms:.0f}ms")
    print(f"   Findings: {task.finding_count}")
    if task.input_summary:
        try:
            summary = json.loads(task.input_summary)
            print(f"   Files Changed: {summary.get('files_changed', 'N/A')}")
            print(f"   Additions: {summary.get('total_additions', 'N/A')}")
            print(f"   Deletions: {summary.get('total_deletions', 'N/A')}")
        except json.JSONDecodeError:
            pass
    if task.severity_distribution:
        try:
            dist = json.loads(task.severity_distribution)
            print(f"   Severity: Critical={dist.get('critical', 0)}, "
                  f"Warning={dist.get('warning', 0)}, "
                  f"Suggestion={dist.get('suggestion', 0)}")
        except json.JSONDecodeError:
            pass
    if task.error_message:
        print(f"   Error: {task.error_message}")
    print()

    # Findings
    findings = repo.get_findings_by_task(task_id)
    if findings:
        print(f"🔍 Findings ({len(findings)}):")
        for f in findings:
            print(f"   [{f.severity.value}] {f.title} — {f.file_path}:L{f.line_number}")
            if f.evidence:
                print(f"       Evidence: {f.evidence[:80]}")
            if f.needs_human_review:
                print(f"       ⚠️  Needs human review")
        print()

    # Sandbox runs
    sandbox_runs = repo.get_sandbox_runs_by_task(task_id)
    if sandbox_runs:
        print(f"⚡ Sandbox Runs ({len(sandbox_runs)}):")
        for s in sandbox_runs:
            status_icon = "✅" if s.status.value == "success" else "❌"
            print(f"   {status_icon} {s.script_name} — {s.status.value} ({s.duration_ms:.0f}ms)")
            if s.error_message:
                print(f"       Error: {s.error_message[:100]}")
        print()

    # Filter logs
    filter_logs = repo.get_filter_logs_by_task(task_id)
    if filter_logs:
        print(f"🔒 Filter Logs ({len(filter_logs)}):")
        for fl in filter_logs:
            print(f"   [{fl.filter_type.value}] {fl.action.value} — {fl.reason or 'No reason'}")
        print()

    # Reports
    reports = repo.get_reports_by_task(task_id)
    if reports:
        print(f"📄 Reports ({len(reports)}):")
        for r in reports:
            print(f"   [{r.report_type.value}] {r.id[:8]}...")
        print()

    # Monitor summary
    monitor = repo.get_monitor_summary(task_id)
    if monitor:
        print(f"📊 Monitoring:")
        print(f"   Total Duration: {monitor.total_duration_ms:.0f}ms")
        print(f"   Sandbox Duration: {monitor.sandbox_duration_ms:.0f}ms")
        print(f"   Tool Calls: {monitor.tool_call_count}")
        print(f"   Intercepts: {monitor.intercept_count}")

    repo.close()


def list_tasks(db_path: str, limit: int = 20) -> None:
    """List recent review tasks.

    Args:
        db_path: Path to the SQLite database.
        limit: Max number of tasks to list.
    """
    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    repo = SqliteCrRepository(db_path)
    tasks = repo.list_tasks(limit=limit)

    if not tasks:
        print("No review tasks found in the database.")
        repo.close()
        return

    print(f"📋 Recent Review Tasks (last {len(tasks)}):")
    print(f"   {'ID':<40} {'Status':<12} {'Findings':<10} {'Duration':<10}")
    print(f"   {'-'*40} {'-'*12} {'-'*10} {'-'*10}")
    for t in tasks:
        print(f"   {t.id:<40} {t.status.value:<12} {t.finding_count:<10} {t.total_duration_ms:<10.0f}ms")
    repo.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="ReviewMind — 数据库查询工具",
    )
    parser.add_argument(
        "task_id", type=str, nargs="?",
        help="Task ID to query",
    )
    parser.add_argument(
        "--db-path", type=str, default=None,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List recent review tasks",
    )

    args = parser.parse_args()
    db_path = args.db_path or DEFAULT_DB_PATH

    if args.list:
        list_tasks(db_path)
    elif args.task_id:
        query_task(args.task_id, db_path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()