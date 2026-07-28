#!/usr/bin/env python3
"""CLI entry point for one automatic review."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.skills_code_review_agent.agent.input_parser import parse_review_input
from examples.skills_code_review_agent.agent.models import ReviewRequest
from examples.skills_code_review_agent.agent.reporting import write_reports
from examples.skills_code_review_agent.agent.review import run_review
from examples.skills_code_review_agent.agent.sanitizer import redact_sensitive_text
from examples.skills_code_review_agent.agent.storage import ReviewRepository


async def create_workspace_runtime(name: str):
    """Create the selected SDK workspace runtime with network disabled where supported."""
    if name == "local":
        from trpc_agent_sdk.code_executors import create_local_workspace_runtime

        return create_local_workspace_runtime()
    if name == "container":
        from trpc_agent_sdk.code_executors import create_container_workspace_runtime

        return create_container_workspace_runtime(host_config={"network_mode": "none"})
    from trpc_agent_sdk.code_executors.cube import (
        CubeClientConfig,
        create_cube_sandbox_client,
        create_cube_workspace_runtime,
    )

    config = CubeClientConfig(execute_timeout=30.0, auto_recover=True)
    client = await create_cube_sandbox_client(config)
    return create_cube_workspace_runtime(
        sandbox_client=client,
        execute_timeout=config.execute_timeout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a policy-governed automatic code review.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--diff-file")
    source.add_argument("--repo-path")
    source.add_argument("--fixture")
    source.add_argument(
        "--file-list",
        help="Text file containing project-relative source paths, one per line.",
    )
    parser.add_argument("--runtime", choices=("container", "cube", "local"), default="container")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--task-id", help="Optional stable task identifier for replay.")
    parser.add_argument("--deterministic-only", action="store_true", default=True)
    parser.add_argument("--fake-model", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--db-url",
        default="sqlite:///examples/skills_code_review_agent/result/review.db",
    )
    parser.add_argument(
        "--output-dir",
        default="examples/skills_code_review_agent/result",
    )
    return parser


async def _main(args: argparse.Namespace) -> int:
    review_input = parse_review_input(
        diff_file=args.diff_file,
        repo_path=args.repo_path,
        fixture_path=args.fixture,
        file_list=args.file_list,
    )
    if args.db_url == "sqlite:///examples/skills_code_review_agent/result/review.db":
        Path("examples/skills_code_review_agent/result").mkdir(
            parents=True,
            exist_ok=True,
        )
    repository = ReviewRepository(args.db_url)
    repository.initialize()
    runtime = await create_workspace_runtime(args.runtime)
    try:
        report = await run_review(
            ReviewRequest(
                review_input=review_input,
                runtime=args.runtime,
                dry_run=args.dry_run,
                fake_model=True,
                task_id=args.task_id,
            ),
            runtime=runtime,
            repository=repository,
        )
    finally:
        if args.runtime == "cube":
            await runtime.destroy()
    paths = write_reports(report, args.output_dir)
    print(f"{report.conclusion}\nJSON: {paths.json_path}\nMarkdown: {paths.markdown_path}")
    return 0 if report.status in {"completed", "partial"} else 1


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_main(build_parser().parse_args(argv)))
    except Exception as exc:
        print(f"review failed: {redact_sensitive_text(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
