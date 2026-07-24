"""Unified diff and repository input parsing."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from .models import ChangedFile
from .models import ChangedLine
from .models import DiffHunk


HUNK_RE = re.compile(r"@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)")


def diff_sha256(diff_text: str) -> str:
    """Return a stable hash for raw diff text."""
    return hashlib.sha256(diff_text.encode("utf-8", errors="replace")).hexdigest()


def normalize_diff_path(path: str) -> str:
    """Normalize a path read from diff metadata."""
    value = path.strip()
    if value in {"/dev/null", "dev/null"}:
        return ""
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    return value


def parse_unified_diff(diff_text: str) -> list[ChangedFile]:
    """Parse a unified diff into changed files, hunks and line numbers."""
    files: list[ChangedFile] = []
    current_file: ChangedFile | None = None
    current_hunk: DiffHunk | None = None
    old_line: int | None = None
    new_line: int | None = None
    pending_old_path = ""

    for raw in diff_text.replace("\r\n", "\n").splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split()
            if len(parts) >= 4:
                pending_old_path = normalize_diff_path(parts[2])
            current_hunk = None
            continue

        if raw.startswith("--- "):
            pending_old_path = normalize_diff_path(raw[4:].split("\t", 1)[0])
            current_hunk = None
            continue

        if raw.startswith("+++ "):
            new_path = normalize_diff_path(raw[4:].split("\t", 1)[0])
            current_file = ChangedFile(
                old_path=pending_old_path,
                new_path=new_path,
                is_deleted=not new_path,
                is_new=not pending_old_path,
            )
            files.append(current_file)
            current_hunk = None
            continue

        match = HUNK_RE.match(raw)
        if match:
            if current_file is None:
                current_file = ChangedFile(old_path=pending_old_path, new_path=pending_old_path)
                files.append(current_file)
            old_start = int(match.group("old"))
            old_count = int(match.group("old_count") or "1")
            new_start = int(match.group("new"))
            new_count = int(match.group("new_count") or "1")
            old_line = old_start
            new_line = new_start
            current_hunk = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                section=match.group("section").strip(),
            )
            current_file.hunks.append(current_hunk)
            continue

        if current_file is None or current_hunk is None:
            continue

        if raw.startswith("+") and not raw.startswith("+++ "):
            current_hunk.lines.append(
                ChangedLine(
                    file=current_file.path,
                    old_line=None,
                    new_line=new_line,
                    kind="+",
                    content=raw[1:],
                ))
            new_line = (new_line or 0) + 1
        elif raw.startswith("-") and not raw.startswith("--- "):
            current_hunk.lines.append(
                ChangedLine(
                    file=current_file.path,
                    old_line=old_line,
                    new_line=None,
                    kind="-",
                    content=raw[1:],
                ))
            old_line = (old_line or 0) + 1
        else:
            content = raw[1:] if raw.startswith(" ") else raw
            current_hunk.lines.append(
                ChangedLine(
                    file=current_file.path,
                    old_line=old_line,
                    new_line=new_line,
                    kind=" ",
                    content=content,
                ))
            old_line = (old_line or 0) + 1
            new_line = (new_line or 0) + 1

    return files


def read_diff_file(path: Path) -> str:
    """Read a diff file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def read_repo_diff(repo_path: Path) -> str:
    """Read local git working tree changes as a unified diff."""
    command = ["git", "-C", str(repo_path), "diff", "--no-ext-diff", "--unified=80"]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(f"git diff failed: {completed.stderr.strip()}")
    return completed.stdout


def read_path_list_diff(repo_path: Path, path_list_file: Path) -> str:
    """Read a path list and return a combined git diff for those paths."""
    paths = [line.strip() for line in path_list_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not paths:
        return ""
    command = ["git", "-C", str(repo_path), "diff", "--no-ext-diff", "--unified=80", "--", *paths]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(f"git diff for path list failed: {completed.stderr.strip()}")
    return completed.stdout

