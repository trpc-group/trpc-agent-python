# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Review input loading for the code review dry-run example."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .diff_parser import parse_unified_diff
from .diff_parser import read_diff_file
from .filters import redact_text
from .schemas import ParsedDiff
from .schemas import ReviewInput


@dataclass(frozen=True)
class ReviewInputBundle:
    """Loaded review input text plus redacted summary metadata."""

    diff_text: str
    parsed_diff: ParsedDiff
    review_input: ReviewInput


def load_review_input(
    *,
    diff_file: Path | None = None,
    repo_path: Path | None = None,
    base_ref: str | None = None,
) -> ReviewInputBundle:
    """Load exactly one review input mode."""
    if bool(diff_file) == bool(repo_path):
        raise ValueError("exactly one of diff_file or repo_path must be provided")

    if diff_file is not None:
        if not diff_file.is_file():
            raise FileNotFoundError(f"diff file not found: {diff_file}")
        diff_text = read_diff_file(diff_file)
        input_type = "diff_file"
        repo_value = None
        diff_file_value = str(diff_file)
    else:
        assert repo_path is not None
        if not repo_path.is_dir():
            raise FileNotFoundError(f"repo path not found: {repo_path}")
        diff_text = read_repo_diff(repo_path, base_ref=base_ref)
        input_type = "repo_path"
        repo_value = str(repo_path)
        diff_file_value = None

    parsed_diff = parse_unified_diff(diff_text)
    review_input = build_review_input(
        parsed_diff,
        diff_text=diff_text,
        input_type=input_type,
        repo_path=repo_value,
        diff_file=diff_file_value,
        base_ref=base_ref,
    )
    return ReviewInputBundle(diff_text=diff_text, parsed_diff=parsed_diff, review_input=review_input)


def read_repo_diff(repo_path: Path, *, base_ref: str | None = None) -> str:
    """Read a git diff from a local repository."""
    command = ["git", "diff", "--no-ext-diff", "--no-color"]
    if base_ref:
        command.append(f"{base_ref}...HEAD")
    result = subprocess.run(command, cwd=repo_path, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.stdout


def build_review_input(
    parsed_diff: ParsedDiff,
    *,
    diff_text: str,
    input_type: str,
    repo_path: str | None = None,
    diff_file: str | None = None,
    base_ref: str | None = None,
) -> ReviewInput:
    """Build a redacted input summary from a parsed diff."""
    changed_files = [path for path in (_file_path(diff_file_entry) for diff_file_entry in parsed_diff.files) if path]
    redacted_diff = redact_text(diff_text)
    digest = hashlib.sha256(redacted_diff.encode("utf-8")).hexdigest()
    summary = (
        f"{len(parsed_diff.files)} file(s), {parsed_diff.hunk_count} hunk(s), "
        f"{parsed_diff.changed_line_count} parsed changed/context line(s)."
    )
    return ReviewInput(
        input_type=input_type,
        repo_path=repo_path,
        diff_file=diff_file,
        base_ref=base_ref,
        changed_files=changed_files,
        diff_sha256=digest,
        diff_summary=summary,
    )


def _file_path(diff_file: object) -> str | None:
    new_path = getattr(diff_file, "new_path", None)
    old_path = getattr(diff_file, "old_path", None)
    return new_path or old_path
