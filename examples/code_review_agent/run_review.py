#!/usr/bin/env python3
"""Run a local, diff-only code review."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import asdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.code_review_agent.code_review.models import ReviewStatus
from examples.code_review_agent.code_review.orchestrator import ReviewConfig, run_review
from examples.code_review_agent.code_review.reporter import write_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review the Git diff between two revisions.")
    parser.add_argument("--repo", default=".", help="Path to a local Git work tree.")
    parser.add_argument("--base", required=True, help="Base commit or revision.")
    parser.add_argument("--head", default="HEAD", help="Head commit or revision (default: HEAD).")
    parser.add_argument("--output-dir", default=".code-review", help="Report output directory.")
    parser.add_argument("--no-llm", action="store_true", help="Collect and report the diff without calling a model.")
    parser.add_argument(
        "--direct-base",
        action="store_true",
        help="Compare the exact base commit instead of its merge base with head.",
    )
    parser.add_argument("--context-lines", type=int, default=3)
    parser.add_argument("--max-files", type=int, default=40)
    parser.add_argument("--max-file-chars", type=int, default=24_000)
    parser.add_argument("--max-total-chars", type=int, default=120_000)
    parser.add_argument("--minimum-confidence", type=float, default=0.0)
    parser.add_argument(
        "--no-static-analysis",
        action="store_true",
        help="Disable Ruff and Bandit execution.",
    )
    parser.add_argument(
        "--static-runtime",
        choices=("local", "docker"),
        default="local",
        help="Run static analyzers locally or in the hardened Docker image.",
    )
    parser.add_argument("--run-tests", action="store_true", help="Also run Pytest in the selected static runtime.")
    parser.add_argument(
        "--strict-static-tools",
        action="store_true",
        help="Fail the review when an enabled analyzer is missing or fails.",
    )
    parser.add_argument("--static-timeout", type=float, default=120.0)
    parser.add_argument("--docker-image", default="trpc-code-review:latest")
    parser.add_argument(
        "--database-url",
        help=("Synchronous SQLAlchemy URL. Defaults to CODE_REVIEW_DATABASE_URL or "
              "<output-dir>/reviews.db using SQLite."),
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not persist the review run to the database.",
    )
    return parser


async def async_main(args: argparse.Namespace) -> int:
    reviewer = None
    model_name = ""
    if not args.no_llm:
        from examples.code_review_agent.agent.reviewer import review_with_llm

        reviewer = review_with_llm
        model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")

    static_analyzer = None
    static_config = None
    if not args.no_static_analysis:
        from examples.code_review_agent.code_review.static_analysis import (
            StaticAnalysisConfig,
            StaticAnalyzer,
        )

        static_config = StaticAnalysisConfig(
            runtime=args.static_runtime,
            run_tests=args.run_tests,
            strict_tools=args.strict_static_tools,
            timeout_seconds=args.static_timeout,
            docker_image=args.docker_image,
        )
        analyzer = StaticAnalyzer(static_config)
        static_analyzer = analyzer.analyze

    config = ReviewConfig(
        context_lines=args.context_lines,
        use_merge_base=not args.direct_base,
        max_files=args.max_files,
        max_patch_chars_per_file=args.max_file_chars,
        max_total_chars=args.max_total_chars,
        minimum_confidence=args.minimum_confidence,
    )
    review_run = await run_review(
        repository=args.repo,
        base_revision=args.base,
        head_revision=args.head,
        config=config,
        reviewer=reviewer,
        static_analyzer=static_analyzer,
        model_name=model_name,
        execution_config={
            "llm_enabled": reviewer is not None,
            "model_base_url": os.getenv("TRPC_AGENT_BASE_URL", "") if reviewer is not None else "",
            "static_analysis": asdict(static_config) if static_config is not None else None,
        },
    )
    persistence_error = ""
    persistence_created: bool | None = None
    if not args.no_persist:
        from examples.code_review_agent.code_review.database import (
            ReviewStore,
            sqlite_database_url,
        )

        database_url = (args.database_url or os.getenv("CODE_REVIEW_DATABASE_URL")
                        or sqlite_database_url(Path(args.output_dir) / "reviews.db"))
        store = None
        try:
            store = ReviewStore(database_url)
            save_result = store.save_run(review_run)
            review_run = save_result.review_run
            persistence_created = save_result.created
        except Exception as exc:  # noqa: BLE001 - preserve a report when persistence fails
            persistence_error = str(exc)
            review_run.diagnostics.append(f"Persistence failed: {exc}")
        finally:
            if store is not None:
                store.close()

    json_path, markdown_path = write_reports(review_run, args.output_dir)
    print(f"Status: {review_run.status.value}")
    print(f"Changed files: {len(review_run.changed_files)}")
    print(f"Findings: {len(review_run.output.findings)}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if persistence_created is not None:
        action = "created" if persistence_created else "reused existing idempotent run"
        print(f"Persistence: {action}")
        print(f"Persisted run ID: {review_run.id}")
    if persistence_error:
        print(f"Persistence error: {persistence_error}", file=sys.stderr)
    if review_run.error_message:
        print(f"Error: {review_run.error_message}", file=sys.stderr)
    return 0 if review_run.status == ReviewStatus.COMPLETED and not persistence_error else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0.0 <= args.minimum_confidence <= 1.0:
        raise SystemExit("--minimum-confidence must be between 0 and 1")
    if args.context_lines < 0 or args.max_files < 1 or args.max_file_chars < 1 or args.max_total_chars < 1:
        raise SystemExit("context and file limits must be positive")
    if args.static_timeout <= 0:
        raise SystemExit("--static-timeout must be positive")
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
