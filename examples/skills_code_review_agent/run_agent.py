#!/usr/bin/env python3
"""CLI entry point for the skills code review agent example."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.skills_code_review_agent.agent.agent import create_agent
from examples.skills_code_review_agent.agent.config import (
    DEFAULT_DB_PATH,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RUNTIME_TYPE,
    ReviewAgentConfig,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the example entry point."""

    parser = argparse.ArgumentParser(
        description="Run the skills code review agent example."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--diff-file",
        help="Path to a unified diff file to review.",
    )
    source_group.add_argument(
        "--repo-path",
        help="Repository path whose current git workspace diff should be reviewed.",
    )
    source_group.add_argument(
        "--fixture",
        help="Fixture diff path used for local testing.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where future report files will be written.",
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="SQLite database path used by the review pipeline.",
    )
    parser.add_argument(
        "--runtime",
        default=DEFAULT_RUNTIME_TYPE,
        choices=["local", "container", "cube", "e2b"],
        help="Execution runtime for sandboxed steps.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline without any external model dependency.",
    )
    parser.add_argument(
        "--fake-model",
        action="store_true",
        help="Enable fake-model mode for deterministic local testing.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the example runner."""

    return build_parser().parse_args(argv)


def _json_default(value: object) -> object:
    """Serialize enums and paths for CLI preview output."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main(argv: list[str] | None = None) -> int:
    """Run the example entry point."""

    args = parse_args(argv)
    config = ReviewAgentConfig(
        diff_file=args.diff_file,
        repo_path=args.repo_path,
        fixture_path=args.fixture,
        output_dir=Path(args.output_dir),
        db_path=Path(args.db_path),
        runtime=args.runtime,
        dry_run=args.dry_run,
        fake_model=args.fake_model,
    )
    agent = create_agent(config)
    task, report = agent.run()

    print(
        json.dumps(
            {
                "task_id": task.task_id,
                "status": task.status.value,
                "input_kind": task.review_input.kind.value,
                "changed_files": task.parsed_diff.changed_paths if task.parsed_diff else [],
                "report": asdict(report),
            },
            indent=2,
            default=_json_default,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
