# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""A long-running tool that streams progress events to the user.

The tool simulates a multi-step site crawl. Each step yields a structured
progress payload that the framework surfaces as a partial Event in real time.
The **last** yielded value is also the final tool response fed back to the LLM.
"""

from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator


async def crawl_site(url: str, max_pages: int = 5) -> AsyncIterator[dict]:
    """Crawl ``url`` and stream progress for every page fetched.

    Use this for long-running fetches where the user benefits from seeing
    incremental progress instead of staring at a spinner.

    Args:
        url: The site URL to crawl (any string for demo purposes).
        max_pages: How many pages to simulate fetching. Defaults to 5.

    Yields:
        dict: One progress payload per step. The final payload is also the
        return value the LLM sees.
    """
    yield {"status": "started", "url": url, "max_pages": max_pages}

    fetched_titles: list[str] = []
    for page_index in range(1, max_pages + 1):
        # Simulate variable per-page latency so the streaming is observable.
        await asyncio.sleep(random.uniform(0.4, 1.2))
        title = f"{url} - page {page_index}"
        fetched_titles.append(title)
        yield {
            "status": "fetched",
            "page": page_index,
            "total": max_pages,
            "title": title,
            "progress": round(page_index / max_pages, 2),
        }

    yield {
        "status": "done",
        "url": url,
        "pages_fetched": len(fetched_titles),
        "titles": fetched_titles,
    }
