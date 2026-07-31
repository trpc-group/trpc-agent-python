#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the deterministic skills code-review example pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

try:
    from .agent.input_parser import InputParseError
    from .agent.models import InputType
    from .agent.models import RuntimeKind
    from .agent.pipeline import ReviewPipelineConfig
    from .agent.pipeline import run_review_pipeline
    from .agent.skill_loader import SkillLoadError
except ImportError:  # pragma: no cover - supports direct script execution
    from agent.input_parser import InputParseError
    from agent.models import InputType
    from agent.models import RuntimeKind
    from agent.pipeline import ReviewPipelineConfig
    from agent.pipeline import run_review_pipeline
    from agent.skill_loader import SkillLoadError


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description="Run the deterministic skills code-review example.")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--diff-file", help="Path to a unified diff or patch file.")
    input_group.add_argument("--repo-path", help="Path to a Git repository with worktree changes.")
    input_group.add_argument("--fixture", help="Fixture name or explicit fixture path.")
    input_group.add_argument("--file-list", help="Path to a UTF-8 file list to review as added files.")
    parser.add_argument("--output-dir", default="output", help="Directory for review artifacts.")
    parser.add_argument("--db-path",
                        default="output/review.sqlite3",
                        help="SQLite path. Default follows --output-dir as review.sqlite3 unless explicitly set.")
    parser.add_argument("--dry-run",
                        action="store_true",
                        default=True,
                        help="Keep execution in non-sandbox dry-run mode.")
    parser.add_argument(
        "--runtime",
        choices=[runtime.value for runtime in RuntimeKind],
        default=RuntimeKind.DRY_RUN.value,
        help="Requested runtime for rule execution. Default: dry-run.",
    )
    parser.add_argument("--allow-local", action="store_true", help="Allow the unsafe local-dev runtime.")
    parser.add_argument("--timeout-sec",
                        type=float,
                        default=30.0,
                        help="Maximum rule-runner execution time in seconds.")
    parser.add_argument("--output-limit-bytes",
                        type=int,
                        default=65536,
                        help="Maximum captured stdout/stderr bytes per stream.")
    parser.add_argument("--container-image",
                        default="python:3-slim",
                        help="Container image for --runtime container. Default: python:3-slim.")
    parser.add_argument("--docker-base-url",
                        default="",
                        help="Optional Docker daemon base URL for --runtime container.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the review pipeline."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    input_type, input_ref = _resolve_input(args)
    config = ReviewPipelineConfig(
        input_type=input_type,
        input_ref=input_ref,
        output_dir=args.output_dir,
        db_path=_resolve_db_path(args),
        runtime=RuntimeKind(args.runtime),
        allow_local=args.allow_local,
        timeout_sec=args.timeout_sec,
        output_limit_bytes=args.output_limit_bytes,
        container_image=args.container_image,
        docker_base_url=args.docker_base_url,
    )

    try:
        result = run_review_pipeline(config)
    except (InputParseError, SkillLoadError, OSError, UnicodeError, ValueError, RuntimeError) as ex:
        parser.error(str(ex))

    print("Code review pipeline completed.")
    print(f"task_id={result.task.id}")
    print(f"input_type={input_type.value}")
    print(f"files={result.input_summary.file_count}")
    print(f"hunks={result.input_summary.hunk_count}")
    print(f"runtime={args.runtime}")
    for name in (
            "parsed_input",
            "skill_manifest",
            "rule_result",
            "findings",
            "filter_events",
            "sandbox_runs",
            "review_report_json",
            "review_report_md",
            "db_path",
    ):
        print(f"{name}={result.artifact_paths[name]}")
    return 0


def _resolve_input(args: argparse.Namespace) -> tuple[InputType, str]:
    if args.diff_file:
        return InputType.DIFF_FILE, args.diff_file
    if args.repo_path:
        return InputType.REPO_PATH, args.repo_path
    if args.file_list:
        return InputType.FILE_LIST, args.file_list
    return InputType.FIXTURE, args.fixture


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.runtime == RuntimeKind.LOCAL_DEV.value and not args.allow_local:
        parser.error("--runtime local-dev requires --allow-local")
    if not str(args.output_dir).strip():
        parser.error("--output-dir must not be empty")
    if not str(args.db_path).strip():
        parser.error("--db-path must not be empty")
    if args.timeout_sec <= 0:
        parser.error("--timeout-sec must be greater than 0")
    if args.output_limit_bytes <= 0:
        parser.error("--output-limit-bytes must be greater than 0")
    if not str(args.container_image).strip():
        parser.error("--container-image must not be empty")
    if _resolve_db_path(args).expanduser().is_dir():
        parser.error("--db-path must be a file path")


def _resolve_db_path(args: argparse.Namespace) -> Path:
    if args.db_path == "output/review.sqlite3":
        return Path(args.output_dir) / "review.sqlite3"
    return Path(args.db_path)


if __name__ == "__main__":
    raise SystemExit(main())
