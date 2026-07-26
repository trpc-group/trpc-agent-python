#!/usr/bin/env python3
"""Run or query the Skills code-review Agent example."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from trpc_agent_sdk.models import OpenAIModel

from agent.constants import DB_URL_ENV
from agent.constants import DEFAULT_DB_URL
from agent.constants import DEFAULT_OUTPUT_DIR
from agent.input_parser import GitDiffOptions
from agent.input_parser import InputValidationError
from agent.input_parser import load_diff_file
from agent.input_parser import load_file_list
from agent.input_parser import load_repo_diff
from agent.models import InputKind
from agent.pipeline import FAKE_MODEL_NAME
from agent.pipeline import FakeReviewModel
from agent.pipeline import PipelineDependencies
from agent.pipeline import ReviewPipeline
from agent.policy import SecretRedactor
from agent.sandbox import create_runtime
from agent.storage import ReviewStore

EXAMPLE_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = EXAMPLE_ROOT / "fixtures"
SKILL_ROOT = EXAMPLE_ROOT / "skills"
DEFAULT_MODEL_NAME = "deepseek-chat"
MODEL_NAME_ENV = "MODEL_NAME"
API_KEY_ENV = "OPENAI_API_KEY"
BASE_URL_ENV = "OPENAI_BASE_URL"


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--diff-file", type=Path)
    inputs.add_argument("--file-list", type=Path)
    inputs.add_argument("--repo-path", type=Path)
    inputs.add_argument("--fixture")
    git_mode = parser.add_mutually_exclusive_group()
    git_mode.add_argument("--staged", action="store_true")
    git_mode.add_argument("--worktree", action="store_true")
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--runtime", choices=("container", "local"), default="container")
    parser.add_argument("--fake-model", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-url", default=os.getenv(DB_URL_ENV, DEFAULT_DB_URL))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model-name", default=os.getenv(MODEL_NAME_ENV, DEFAULT_MODEL_NAME))
    parser.add_argument("--api-key", default=os.getenv(API_KEY_ENV))
    parser.add_argument("--base-url", default=os.getenv(BASE_URL_ENV))


def parse_args() -> argparse.Namespace:
    """Parse run/show subcommands."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    _add_run_arguments(commands.add_parser("run", help="run a review"))
    show = commands.add_parser("show", help="query a persisted task")
    show.add_argument("--task-id", required=True)
    show.add_argument("--db-url", default=os.getenv(DB_URL_ENV, DEFAULT_DB_URL))
    return parser.parse_args()


def _fixture_path(name: str) -> Path:
    if not name or Path(name).name != name:
        raise InputValidationError("fixture must be a simple name")
    path = FIXTURE_ROOT / f"{name}.diff"
    if not path.is_file():
        raise InputValidationError(f"unknown fixture: {name}")
    return path


def _load_input(args: argparse.Namespace):
    has_git_options = args.staged or args.worktree or args.base or args.head
    if has_git_options and not args.repo_path:
        raise InputValidationError("Git range options require --repo-path")
    if args.diff_file:
        return load_diff_file(args.diff_file)
    if args.file_list:
        return load_file_list(args.file_list)
    if args.repo_path:
        options = GitDiffOptions(
            staged=args.staged,
            worktree=args.worktree,
            base=args.base,
            head=args.head,
        )
        return load_repo_diff(args.repo_path, options)
    return load_diff_file(_fixture_path(args.fixture)).model_copy(update={
        "kind": InputKind.FIXTURE,
        "source": args.fixture
    }, )


def _create_model(args: argparse.Namespace):
    if args.fake_model or args.dry_run:
        return FakeReviewModel(model_name=FAKE_MODEL_NAME)
    if not args.api_key:
        raise ValueError("real model mode requires --api-key or OPENAI_API_KEY")
    return OpenAIModel(
        model_name=args.model_name,
        api_key=args.api_key,
        base_url=args.base_url,
    )


async def _run(args: argparse.Namespace) -> int:
    redactor = SecretRedactor()
    store = ReviewStore(args.db_url, redactor)
    await store.initialize()
    try:
        dependencies = PipelineDependencies(
            store=store,
            runtime=create_runtime(args.runtime),
            skill_root=SKILL_ROOT,
            output_dir=args.output_dir,
            model=_create_model(args),
        )
        report, json_path, markdown_path = await ReviewPipeline(dependencies).run(
            _load_input(args),
            dry_run=args.dry_run,
        )
        print(f"task_id={report.task_id}")
        print(f"status={report.status.value}")
        print(f"json_report={json_path}")
        print(f"markdown_report={markdown_path}")
        return 0 if report.status.value in {"complete", "partial"} else 1
    finally:
        await store.close()


async def _show(args: argparse.Namespace) -> int:
    store = ReviewStore(args.db_url, SecretRedactor())
    await store.initialize()
    try:
        result = await store.get_report(args.task_id)
        if result is None:
            print("task not found")
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        await store.close()


async def async_main(args: argparse.Namespace) -> int:
    """Dispatch one CLI command."""
    if args.command == "show":
        return await _show(args)
    return await _run(args)


def main() -> int:
    """Run CLI with redacted failure output."""
    try:
        return asyncio.run(async_main(parse_args()))
    except Exception as exc:  # pylint: disable=broad-except
        message = SecretRedactor().redact_text(str(exc))
        print(f"error: {type(exc).__name__}: {message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
