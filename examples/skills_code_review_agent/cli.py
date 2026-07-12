"""CLI for diff files, repositories, file lists, and database lookup."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent import CodeReviewAgent
from diff_parser import DiffParser


HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--diff-file", type=Path)
    source.add_argument("--repo-path", type=Path)
    source.add_argument("--files", type=Path, nargs="+")
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--database", type=Path, default=HERE / "reviews.db")
    parser.add_argument("--policy", type=Path, default=HERE / "review_policy.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Use fake sandbox; no model or Docker required")
    args = parser.parse_args()
    if not any((args.diff_file, args.repo_path, args.files)):
        parser.error("one of --diff-file, --repo-path, or --files is required")

    agent = CodeReviewAgent(
        root=args.repo_path or Path.cwd(),
        database=args.database,
        policy=args.policy,
        dry_run=args.dry_run,
    )
    if args.diff_file:
        report = agent.review_file(args.diff_file, args.output_dir)
    elif args.repo_path:
        report = agent.review_repo(args.repo_path, args.output_dir)
    else:
        diff, _ = DiffParser.from_paths(args.files)
        report = agent.review_diff(diff, args.output_dir)
    print(f"task_id={report.task_id} conclusion={report.conclusion}")
    return 0 if report.status.startswith("completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
