#!/usr/bin/env python3
"""Run durable GitHub code review workers."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.code_review_agent.github_integration.runtime import (
    build_service_from_environment,
    build_store_from_environment,
)
from examples.code_review_agent.github_integration.worker import GitHubReviewWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run durable GitHub review jobs.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("GITHUB_REVIEW_WORKER_CONCURRENCY", "1")),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("GITHUB_REVIEW_POLL_SECONDS", "2")),
    )
    parser.add_argument(
        "--lease-seconds",
        type=float,
        default=float(os.getenv("GITHUB_REVIEW_LEASE_SECONDS", "300")),
    )
    return parser


async def run_workers(*, concurrency: int, poll_seconds: float, lease_seconds: float) -> None:
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    store = build_store_from_environment()
    service = build_service_from_environment(store)
    workers = [
        GitHubReviewWorker(
            store=store,
            service=service,
            worker_id=f"worker-{os.getpid()}-{index + 1}",
            poll_seconds=poll_seconds,
            lease_seconds=lease_seconds,
            base_retry_seconds=float(os.getenv("GITHUB_REVIEW_RETRY_BASE_SECONDS", "5")),
            max_retry_seconds=float(os.getenv("GITHUB_REVIEW_RETRY_MAX_SECONDS", "300")),
        )
        for index in range(concurrency)
    ]
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, lambda: [worker.stop() for worker in workers])
        except NotImplementedError:
            pass
    try:
        await asyncio.gather(*(worker.run_forever() for worker in workers))
    finally:
        close = getattr(service.token_provider, "close", None)
        if close is not None:
            await close()
        store.close()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=os.getenv("GITHUB_WORKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(
        run_workers(
            concurrency=args.concurrency,
            poll_seconds=args.poll_seconds,
            lease_seconds=args.lease_seconds,
        )
    )


if __name__ == "__main__":
    main()
