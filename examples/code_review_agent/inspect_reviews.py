#!/usr/bin/env python3
"""List and inspect persisted code review runs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.code_review_agent.code_review.database import (
    ReviewStore,
    sqlite_database_url,
)
from examples.code_review_agent.code_review.models import ReviewStatus
from examples.code_review_agent.code_review.reporter import render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect persisted code review runs.")
    parser.add_argument(
        "--database-url",
        help=("Synchronous SQLAlchemy URL. Defaults to CODE_REVIEW_DATABASE_URL or "
              ".code-review/reviews.db using SQLite."),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List recent review runs.")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--repository")
    list_parser.add_argument("--status", choices=tuple(status.value for status in ReviewStatus))

    show_parser = subparsers.add_parser("show", help="Show one complete review run.")
    show_parser.add_argument("run_id")
    show_parser.add_argument("--format", choices=("json", "markdown"), default="markdown")

    delivery_parser = subparsers.add_parser("deliveries", help="List recent GitHub webhook deliveries.")
    delivery_parser.add_argument("--limit", type=int, default=20)
    delivery_parser.add_argument(
        "--status",
        choices=("received", "queued", "processing", "retrying", "completed", "failed", "ignored"),
    )
    delivery_parser.add_argument("--repository")

    jobs_parser = subparsers.add_parser("jobs", help="List durable GitHub review jobs.")
    jobs_parser.add_argument("--limit", type=int, default=20)
    jobs_parser.add_argument(
        "--status",
        choices=("queued", "leased", "succeeded", "dead"),
    )

    replay_parser = subparsers.add_parser("replay", help="Requeue one dead GitHub review job.")
    replay_parser.add_argument("delivery_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = (args.database_url or os.getenv("CODE_REVIEW_DATABASE_URL")
                    or sqlite_database_url(Path(".code-review") / "reviews.db"))
    store = ReviewStore(database_url)
    try:
        if args.command == "list":
            status = ReviewStatus(args.status) if args.status else None
            runs = store.list_runs(
                limit=args.limit,
                repository_path=args.repository,
                status=status,
            )
            if not runs:
                print("No persisted review runs.")
                return 0
            print("RUN ID                               STATUS     STARTED                    FILES  FINDINGS")
            for review_run in runs:
                started = review_run.started_at.isoformat(timespec="seconds")
                print(f"{review_run.id:<36} {review_run.status.value:<10} "
                      f"{started:<26} {len(review_run.changed_files):>5}  "
                      f"{len(review_run.output.findings):>8}")
            return 0

        if args.command == "deliveries":
            deliveries = store.list_github_deliveries(
                limit=args.limit,
                status=args.status,
                repository_full_name=args.repository,
            )
            if not deliveries:
                print("No GitHub webhook deliveries.")
                return 0
            print("DELIVERY ID                         STATUS      REPOSITORY                 PR")
            for delivery in deliveries:
                pull_number = delivery["pull_number"] or ""
                print(f"{delivery['delivery_id']:<35} {delivery['status']:<11} "
                      f"{delivery['repository_full_name']:<26} {pull_number}")
            return 0

        if args.command == "jobs":
            jobs = store.list_github_jobs(limit=args.limit, status=args.status)
            if not jobs:
                print("No durable GitHub review jobs.")
                return 0
            print("DELIVERY ID                         STATUS      ATTEMPTS  AVAILABLE")
            for job in jobs:
                available = job["available_at"].isoformat(timespec="seconds")
                attempts = f"{job['attempt_count']}/{job['max_attempts']}"
                print(
                    f"{job['delivery_id']:<35} {job['status']:<11} "
                    f"{attempts:<9} {available}"
                )
            return 0

        if args.command == "replay":
            if not store.replay_github_job(args.delivery_id):
                print(f"Dead GitHub review job not found: {args.delivery_id}")
                return 2
            print(f"Requeued GitHub review job: {args.delivery_id}")
            return 0

        review_run = store.get_run(args.run_id)
        if review_run is None:
            print(f"Review run not found: {args.run_id}")
            return 2
        if args.format == "json":
            print(json.dumps(review_run.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            print(render_markdown(review_run), end="")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
