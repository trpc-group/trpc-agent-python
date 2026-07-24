#!/usr/bin/env python3
"""Run the skill-based code review workflow."""

import argparse
import asyncio
import os
import re
import stat
import sys
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parent
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ENV_BYTES = 64 * 1024
ALLOWED_ENV_PREFIXES = ("CODE_REVIEW_", "TRPC_AGENT_")


def load_env_file(path: Path) -> None:
    """Load private, review-specific settings without overriding the process."""
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(".env must be a regular file, not a symbolic link")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError(".env permissions must not grant group or other access")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        data = source.read(MAX_ENV_BYTES + 1)
    if len(data) > MAX_ENV_BYTES:
        raise ValueError(f".env exceeds {MAX_ENV_BYTES} bytes")
    for line_number, raw_line in enumerate(
        data.decode("utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry at line {line_number}")
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if not ENV_NAME.fullmatch(key):
            raise ValueError(f"Invalid .env key at line {line_number}")
        if not key.startswith(ALLOWED_ENV_PREFIXES):
            raise ValueError(f"Unsupported .env key at line {line_number}")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unterminated .env value at line {line_number}")
            value = value[1:-1]
        # Explicit process variables take precedence over local developer settings.
        os.environ.setdefault(key, value)


def find_git_worktree(start: Path) -> Path:
    """Find the nearest Git worktree without invoking repository code."""
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ValueError(f"No Git worktree contains the current directory: {resolved}")


def build_parser() -> argparse.ArgumentParser:
    """Create the small CLI used by this example."""
    parser = argparse.ArgumentParser(
        description="Review a Git repository with Docker-backed Agent Skills.",
    )
    parser.add_argument("--repo-path", type=Path, help="Git worktree to review")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--diff-file", type=Path, help="unified diff or PR patch")
    inputs.add_argument("--file-list", type=Path, help="newline-delimited relative paths")
    inputs.add_argument("--fixture", help="fixture name under tests/fixtures")
    parser.add_argument(
        "--full",
        action="store_true",
        help="review the full tracked repository instead of changed code only",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite path overriding CODE_REVIEW_SQLITE_PATH (SQLite only)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXAMPLE_ROOT / "reports" / "output",
        help="directory for JSON and Markdown reports",
    )
    parser.add_argument(
        "--docker-image",
        default=None,
        help="Docker image overriding CODE_REVIEW_DOCKER_IMAGE",
    )
    parser.add_argument(
        "--fake-model",
        action="store_true",
        help="use deterministic rules instead of a model API",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="simulate sandbox execution while still writing DB and reports",
    )
    return parser


async def run(args: argparse.Namespace) -> None:
    """Construct dependencies and run one review."""
    from reports.models import ReviewScope
    from agent.config import ReviewLimits
    from reports.writers import ReportWriter
    from storage.factory import create_review_store
    from workflow import CodeReviewWorkflow
    from workflow import ReviewRequest

    scope = ReviewScope.FULL if args.full else ReviewScope.CHANGED
    fake_mode = args.fake_model or args.dry_run
    # Fake and dry-run modes must not construct model or Docker clients.
    if fake_mode:
        model_config = None
        sandbox = None
    else:
        from agent.config import ModelConfig
        from sandbox.factory import create_sandbox_provider

        model_config = ModelConfig.from_env()
        sandbox = create_sandbox_provider(args.docker_image)

    workflow = CodeReviewWorkflow(
        model_config=model_config,
        sandbox=sandbox,
        store=create_review_store(args.database),
        report_writer=ReportWriter(args.output_dir),
        skills_path=EXAMPLE_ROOT / "skills",
        limits=ReviewLimits.from_env(),
    )
    repository_path = args.repo_path
    # With no explicit input, review changed code in the caller's worktree.
    if not any((repository_path, args.diff_file, args.file_list, args.fixture)):
        repository_path = find_git_worktree(Path.cwd())
    result = await workflow.run(
        ReviewRequest(
            repository_path=repository_path,
            diff_file=args.diff_file,
            file_list=args.file_list,
            fixture=args.fixture,
            scope=scope,
            fake_model=args.fake_model,
            dry_run=args.dry_run,
        ),
    )
    print(f"Review completed: {result.report.task_id}")
    print(f"JSON report: {result.artifacts.json_path}")
    print(f"Markdown report: {result.artifacts.markdown_path}")


def main() -> int:
    """CLI entrypoint."""
    args = build_parser().parse_args()
    try:
        load_env_file(EXAMPLE_ROOT / ".env")
        asyncio.run(run(args))
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
