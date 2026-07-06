# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""DB init / migration script.

Usage::

    python run_agent.py init-db [--db-url URL]
    # or standalone:
    python -m codereview.store.init_db --db-url sqlite+aiosqlite:///./review.db

Idempotent: ``SqlStorage.create_sql_engine`` runs ``metadata.create_all`` and
the SDK's forward-only column migration, so re-running after a schema upgrade
adds any new columns without touching existing data.
"""

from __future__ import annotations

import argparse
import asyncio

from .models import ReviewStorageBase
from .sql_store import SqlReviewStore


async def init_db(db_url: str) -> list:
    store = SqlReviewStore(db_url)
    try:
        await store.initialize()
    finally:
        await store.close()
    return sorted(ReviewStorageBase.metadata.tables)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Initialize / migrate the code-review database.")
    parser.add_argument("--db-url", default="sqlite+aiosqlite:///./review.db",
                        help="SQLAlchemy async URL (default: %(default)s)")
    args = parser.parse_args(argv)
    tables = asyncio.run(init_db(args.db_url))
    print(f"database ready at {args.db_url}")
    for table in tables:
        print(f"  - {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
