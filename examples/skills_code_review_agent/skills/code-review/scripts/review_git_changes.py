#!/usr/bin/env python3
"""Collect one Git diff scope and emit paginated rule evidence."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from parse_unified_diff import MAX_DIFF_BYTES
from parse_unified_diff import parse_unified_diff
from run_review_rules import MAX_PAGE_SIZE
from run_review_rules import build_page


def collect_diff(repository: Path, mode: str) -> str:
    """Run one fixed read-only Git diff command with a bounded result."""
    if mode not in {"unstaged", "staged"}:
        raise ValueError(f"unsupported Git diff mode: {mode}")
    repository = repository.resolve()
    if not repository.is_dir() or not (repository / ".git").exists():
        raise ValueError(f"not a Git worktree: {repository}")
    command = ["git", "-C", str(repository), "diff"]
    if mode == "staged":
        command.append("--cached")
    command.extend(("--no-ext-diff", "--no-textconv"))
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace")[:1000]
        raise ValueError(f"Git diff failed: {message}")
    if len(completed.stdout) > MAX_DIFF_BYTES:
        raise ValueError(f"Git diff exceeds {MAX_DIFF_BYTES} bytes")
    return completed.stdout.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--mode", choices=("unstaged", "staged"), required=True)
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--limit", type=int, default=MAX_PAGE_SIZE)
    args = parser.parse_args()
    try:
        diff_text = collect_diff(args.repository, args.mode)
        parsed = parse_unified_diff(diff_text)
        result = build_page(parsed, cursor=args.cursor, limit=args.limit)
        result["mode"] = args.mode
        result["input_digest"] = hashlib.sha256(
            diff_text.encode("utf-8")
        ).hexdigest()
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
