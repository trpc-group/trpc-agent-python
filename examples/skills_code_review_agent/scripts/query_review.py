#!/usr/bin/env python3
"""List and query persisted code-review tasks."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    from ..agent.storage import create_store
except ImportError:  # Allow direct execution from the example directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent.storage import create_store


DEFAULT_DATABASE = "review_agent.sqlite3"
TASK_COLUMNS = (
    "task_id",
    "status",
    "input_type",
    "input_ref",
    "started_at",
    "finished_at",
    "final_conclusion",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect persisted code-review tasks.")
    parser.add_argument("--database", "--db", "--db-path", default=DEFAULT_DATABASE,
                        help="SQLite path or DSN (default: review_agent.sqlite3).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List recent review tasks.")
    list_parser.add_argument("--limit", type=int, default=100)
    list_parser.add_argument("--status", help="Only show tasks with this status.")
    list_parser.add_argument("--format", choices=("table", "json"), default="table")

    query_parser = subparsers.add_parser("query", help="Query one complete task bundle.")
    query_parser.add_argument("task_id")
    query_parser.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = create_store(args.database)
    try:
        if args.command == "list":
            payload: Any = store.list_tasks(limit=args.limit, status=args.status)
            if args.format == "json":
                _print_json(payload)
            else:
                print(_render_table(payload, TASK_COLUMNS))
            return 0

        try:
            payload = store.get_task(args.task_id)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.format == "json":
            _print_json(payload)
        else:
            _print_bundle(payload)
        return 0
    finally:
        store.close()


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _print_bundle(bundle: dict[str, Any]) -> None:
    sections: tuple[tuple[str, list[dict[str, Any]], Sequence[str] | None], ...] = (
        ("Task", [bundle["task"]], TASK_COLUMNS),
        ("Sandbox Runs", bundle["sandbox_runs"], None),
        ("Findings", bundle["findings"], None),
        ("Filter Intercepts", bundle["filter_intercepts"], None),
        ("Metrics", _mapping_rows(bundle["metrics"]), ("key", "value")),
        ("Report", _mapping_rows(bundle["report"]), ("key", "value")),
    )
    for title, rows, columns in sections:
        print(f"\n{title}")
        print(_render_table(rows, columns))


def _mapping_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": item} for key, item in sorted(value.items())]


def _render_table(rows: list[dict[str, Any]], columns: Sequence[str] | None = None) -> str:
    if not rows:
        return "(none)"
    selected = list(columns or _all_columns(rows))
    values = [[_cell(row.get(column)) for column in selected] for row in rows]
    widths = [
        max(len(column), *(len(row[index]) for row in values))
        for index, column in enumerate(selected)
    ]
    header = " | ".join(column.ljust(widths[index]) for index, column in enumerate(selected))
    divider = "-+-".join("-" * width for width in widths)
    body = [" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in values]
    return "\n".join([header, divider, *body])


def _all_columns(rows: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


if __name__ == "__main__":
    raise SystemExit(main())
