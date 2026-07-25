#!/usr/bin/env python3
#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Query a complete persisted review by task id."""

import argparse
import asyncio
import json
from pathlib import Path

from examples.skills_code_review_agent.agent.core import SecretRedactor
from examples.skills_code_review_agent.agent.storage import SqlReviewStore

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]


async def query(db_url: str, task_id: str) -> dict:
    store = SqlReviewStore(db_url)
    await store.initialize()
    try:
        return await store.get_review(task_id)
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--db-url", default=f"sqlite:///{EXAMPLE_ROOT / 'data' / 'reviews.db'}")
    args = parser.parse_args()
    payload = asyncio.run(query(args.db_url, args.task_id))
    clean, _ = SecretRedactor.redact_value(payload)
    print(json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
