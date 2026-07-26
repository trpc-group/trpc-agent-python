#!/usr/bin/env python3
"""Initialize the code-review example database."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_ROOT))


def parse_args() -> argparse.Namespace:
    """Parse database initialization arguments."""
    from agent.constants import DB_URL_ENV
    from agent.constants import DEFAULT_DB_URL

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.getenv(DB_URL_ENV, DEFAULT_DB_URL))
    return parser.parse_args()


async def initialize(db_url: str) -> None:
    """Create missing review tables and close the engine."""
    from agent.storage import create_sql_storage

    storage = create_sql_storage(db_url)
    await storage.create_sql_engine()
    await storage.close()


if __name__ == "__main__":
    asyncio.run(initialize(parse_args().db_url))
