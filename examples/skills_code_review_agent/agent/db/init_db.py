# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Initialize the Code Review Agent database (SDK-backed, async).

Table creation is driven by the ORM metadata in :mod:`db.models` via the
SDK's :class:`SqlStorage` (``metadata.create_all``). Idempotent — safe to
run repeatedly. SQLite ``PRAGMA foreign_keys=ON`` is applied automatically
by the SDK storage layer.

Usage
-----
    python agent/db/init_db.py                  # default ./cr_agent.db
    python agent/db/init_db.py --db-path /tmp/cr.db
    python -m examples.skills_code_review_agent.agent.db.init_db --db-path cr.db
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as a script (no package context).
_HERE = Path(__file__).resolve().parent  # .../agent/db
if str(_HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent.parent))  # project root

from agent.db import SQLiteStore  # noqa: E402
from agent.db.models import CRBase  # noqa: E402

_EXPECTED_TABLES = (
    "review_task",
    "input_diff",
    "sandbox_run",
    "finding",
    "filter_block",
    "monitor_summary",
    "review_report",
)


async def init_db(db_path: str | Path = "cr_agent.db", *, verbose: bool = True) -> None:
    """Create/upgrade the CR Agent database at ``db_path``.

    Safe to call repeatedly — ``metadata.create_all`` only adds missing
    tables, and the SDK applies forward-only column migrations.
    """
    store = SQLiteStore(db_path)
    try:
        # create_sql_engine() runs metadata.create_all + sqlite pragmas.
        await store._ensure_engine()

        if verbose:
            await _report(store, db_path)
    finally:
        await store.close()


async def _report(store: "SQLiteStore", db_path: str | Path) -> None:
    """Print a concise summary of tables now present."""
    from sqlalchemy import inspect as sa_inspect

    await store._ensure_engine()
    engine = store.storage._db_engine  # underlying async engine
    # Run sync inspect via the async engine.
    def _sync_inspect(conn):
        return sa_inspect(conn).get_table_names()

    async with engine.connect() as conn:
        tables = await conn.run_sync(_sync_inspect)
    table_set = {t for t in tables if not t.startswith("sqlite_")}
    missing = [t for t in _EXPECTED_TABLES if t not in table_set]

    print(f"[init_db] database : {db_path}")
    print(f"[init_db] tables   : {len(table_set)} ({', '.join(sorted(table_set))})")
    if missing:
        print(f"[init_db] WARNING missing tables: {missing}", file=sys.stderr)
    else:
        print(f"[init_db] all {len(_EXPECTED_TABLES)} expected tables present")
    print("[init_db] foreign_keys = ON (set by SDK SqlStorage)")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize the Code Review Agent SQLite database (SDK-backed).",
    )
    parser.add_argument(
        "--db-path", default="cr_agent.db",
        help="Path to the SQLite database file (default: cr_agent.db).",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress the summary report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    asyncio.run(init_db(args.db_path, verbose=not args.quiet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
