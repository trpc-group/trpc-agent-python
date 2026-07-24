#!/usr/bin/env python3
"""ReviewMind CLI — 智能代码审查助手

命令行批处理入口，适用于 CI 流水线和批量审查场景。

Usage:
    # 审查 diff 文件
    python run_agent.py --diff-file pr.diff

    # 审查 fixture 样本
    python run_agent.py --fixture 01_clean

    # Dry-run 模式（无 API Key）
    python run_agent.py --dry-run --fixture 01_clean

    # 指定输出目录
    python run_agent.py --diff-file pr.diff --output-dir ./reports/my-review
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure the package is importable
_parent = Path(__file__).resolve().parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from config import ReviewAgentConfig
from progress import print_progress_callback
from review_agent import run_review, mask_secrets
from storage.models import ReviewResult, TaskStatus


def read_diff_file(path: str) -> str:
    """Read a diff file from disk.

    Args:
        path: Path to the diff file.

    Returns:
        The file content as a string.
    """
    if not os.path.exists(path):
        print(f"❌ Diff file not found: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def get_git_diff(repo_path: str) -> str:
    """Get the git diff from a repository workspace.

    Runs `git diff` (unstaged changes) and `git diff --cached` (staged changes)
    in the specified repository path, combining both into a single diff output.

    Args:
        repo_path: Path to the git repository.

    Returns:
        The combined diff content as a string.
    """
    import subprocess

    repo_path = os.path.abspath(repo_path)
    if not os.path.exists(os.path.join(repo_path, ".git")):
        print(f"❌ Not a git repository: {repo_path}")
        sys.exit(1)

    try:
        # Unstaged changes
        result1 = subprocess.run(
            ["git", "diff"],
            capture_output=True, text=True, cwd=repo_path, timeout=30,
        )
        # Staged changes
        result2 = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True, text=True, cwd=repo_path, timeout=30,
        )

        diff = result1.stdout + result2.stdout
        if not diff.strip():
            print(f"⚠️  No changes found in repository: {repo_path}")
            print(f"   Use --diff-file to review a specific diff file instead.")

        return diff

    except subprocess.TimeoutExpired:
        print(f"❌ Git diff timed out for repository: {repo_path}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"❌ Git not found. Please install git.")
        sys.exit(1)


def run_cli_review(
    diff_content: str,
    input_type: str,
    output_dir: str,
    db_path: str,
    dry_run: bool = False,
    verbose: bool = True,
) -> Optional[ReviewResult]:
    """Run the review pipeline from the CLI.

    Args:
        diff_content: The diff content or fixture name.
        input_type: "diff_file" or "fixture".
        output_dir: Output directory for reports.
        db_path: Path to the SQLite database.
        dry_run: If True, skip sandbox execution and LLM calls.
        verbose: If True, print progress messages.

    Returns:
        ReviewResult or None on failure.
    """
    os.makedirs(output_dir, exist_ok=True)

    config = ReviewAgentConfig(
        input_source="fixture" if input_type == "fixture" else "diff_file",
        input_value=diff_content,
        output_dir=output_dir,
        sandbox_type="local",
        dry_run=dry_run,
        fake_model=dry_run,
        db_path=db_path,
    )

    if verbose:
        print(f"🔍 Running code review...")
        print(f"   Input: {'fixture' if input_type == 'fixture' else 'diff'} ({len(diff_content)} bytes)")
        print(f"   Output: {output_dir}")
        print(f"   DB: {db_path}")
        print(f"   Mode: {'dry-run' if dry_run else 'full'}")
        print()

    start = time.time()
    result = run_review(config)
    elapsed = (time.time() - start) * 1000

    if not result:
        print("❌ Review failed: pipeline returned no result")
        return None

    if result.task.status == TaskStatus.FAILED:
        print(f"❌ Review failed: {result.task.error_message}")
        return result

    if verbose:
        print(f"\n✅ Review complete in {elapsed:.0f}ms")
        print(f"   Findings: {len(result.findings)} critical, "
              f"{len(result.warnings)} warnings, "
              f"{len(result.needs_human_review)} needs review")
        print(f"   JSON report: {result.report_path_json}")
        print(f"   MD report:  {result.report_path_md}")

    return result


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="ReviewMind — 智能代码审查助手 (CLI)",
    )

    # Input source (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--diff-file", type=str, default=None,
        help="Path to a unified diff file",
    )
    input_group.add_argument(
        "--fixture", type=str, default=None,
        help="Fixture name (e.g. '01_clean')",
    )
    input_group.add_argument(
        "--repo-path", type=str, default=None,
        help="Path to a git repository workspace (extracts staged + unstaged diff)",
    )

    # Options
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="Output directory for reports (default: ./reports/<name>)",
    )
    parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (default: <output_dir>/review.db)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Dry-run mode: skip sandbox and LLM calls",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", default=True,
        help="Print verbose output",
    )

    args = parser.parse_args()

    # Determine input
    if args.diff_file:
        diff_content = read_diff_file(args.diff_file)
        input_type = "diff_file"
        output_name = os.path.splitext(os.path.basename(args.diff_file))[0]
    elif args.fixture:
        diff_content = args.fixture
        input_type = "fixture"
        output_name = args.fixture
    elif args.repo_path:
        diff_content = get_git_diff(args.repo_path)
        input_type = "diff_file"
        output_name = os.path.basename(os.path.abspath(args.repo_path))
    else:
        parser.print_help()
        sys.exit(1)

    # Determine output paths
    output_dir = args.output_dir or os.path.join(os.getcwd(), "reports", output_name)
    db_path = args.db_path or os.path.join(output_dir, "review.db")

    # Run the review
    result = run_cli_review(
        diff_content=diff_content,
        input_type=input_type,
        output_dir=output_dir,
        db_path=db_path,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    if result and result.task.status == TaskStatus.COMPLETED:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()