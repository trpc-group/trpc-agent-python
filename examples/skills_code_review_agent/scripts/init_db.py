#!/usr/bin/env python3
"""Initialize the code-review database schema."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

try:
    from ..agent.storage import SqliteReviewStore, create_store
except ImportError:  # Allow direct execution from the example directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent.storage import SqliteReviewStore, create_store


DEFAULT_DATABASE = "review_agent.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize the code-review persistence schema.")
    parser.add_argument("database", nargs="?", help="SQLite path or DSN (default: review_agent.sqlite3).")
    parser.add_argument("--database", "--db", "--db-path", dest="database_option",
                        help="SQLite path or DSN; overrides the positional value.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable initialization details.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = args.database_option or args.database or DEFAULT_DATABASE
    store = create_store(database)
    try:
        # Constructors initialize automatically for application compatibility;
        # invoking the idempotent method here makes this script's intent clear.
        store.init_schema()
        backend = "sqlite" if isinstance(store, SqliteReviewStore) else type(store).__name__
        location = str(store.db_path) if isinstance(store, SqliteReviewStore) else database
    finally:
        store.close()

    payload = {"backend": backend, "database": location, "status": "initialized"}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Initialized {backend} review database: {location}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
