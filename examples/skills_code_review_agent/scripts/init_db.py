#!/usr/bin/env python3
#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Initialize the code review SQL schema."""

import argparse
import asyncio
from pathlib import Path

from examples.skills_code_review_agent.agent.storage import SqlReviewStore

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]


async def initialize(db_url: str) -> None:
    store = SqlReviewStore(db_url)
    await store.initialize()
    await store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", default=f"sqlite:///{EXAMPLE_ROOT / 'data' / 'reviews.db'}")
    args = parser.parse_args()
    asyncio.run(initialize(args.db_url))
    print(f"Initialized review database: {args.db_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
