# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Small unified diff parser for the code review dry-run example."""

from __future__ import annotations

import re
from pathlib import Path

from .schemas import ChangedLine
from .schemas import ChangedLineKind
from .schemas import DiffFile
from .schemas import DiffHunk
from .schemas import ParsedDiff

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$"
)


def read_diff_file(path: Path) -> str:
    """Read a unified diff file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def parse_unified_diff(diff_text: str) -> ParsedDiff:
    """Parse a small, git-style unified diff.

    The parser intentionally supports the subset needed by the dry-run MVP:
    git file headers, old/new file markers, hunk headers, line anchors, binary
    markers, and rename metadata.
    """
    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: DiffHunk | None = None
    old_line: int | None = None
    new_line: int | None = None

    def finish_file() -> None:
        nonlocal current_file, current_hunk, old_line, new_line
        if current_file is not None:
            _finalize_status(current_file)
            files.append(current_file)
        current_file = None
        current_hunk = None
        old_line = None
        new_line = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            finish_file()
            old_path, new_path = _parse_git_header(raw_line)
            current_file = DiffFile(old_path=old_path, new_path=new_path, status="modified")
            continue

        if current_file is None:
            continue

        if raw_line.startswith("rename from "):
            current_file.old_path = raw_line.removeprefix("rename from ").strip()
            current_file.status = "renamed"
            continue

        if raw_line.startswith("rename to "):
            current_file.new_path = raw_line.removeprefix("rename to ").strip()
            current_file.status = "renamed"
            continue

        if raw_line.startswith("Binary files ") or raw_line == "GIT binary patch":
            current_file.is_binary = True
            current_file.status = "binary"
            continue

        if raw_line.startswith("--- "):
            current_file.old_path = _normalize_marker_path(raw_line[4:].strip())
            continue

        if raw_line.startswith("+++ "):
            current_file.new_path = _normalize_marker_path(raw_line[4:].strip())
            continue

        hunk_match = _HUNK_RE.match(raw_line)
        if hunk_match:
            current_hunk = DiffHunk(
                old_start=int(hunk_match.group("old_start")),
                old_count=int(hunk_match.group("old_count") or "1"),
                new_start=int(hunk_match.group("new_start")),
                new_count=int(hunk_match.group("new_count") or "1"),
                section=hunk_match.group("section").strip(),
            )
            current_file.hunks.append(current_hunk)
            old_line = current_hunk.old_start
            new_line = current_hunk.new_start
            continue

        if current_hunk is None:
            continue

        if raw_line == r"\ No newline at end of file":
            continue

        prefix = raw_line[:1]
        text = raw_line[1:] if prefix in {" ", "+", "-"} else raw_line

        if prefix == "+":
            current_hunk.changed_lines.append(
                ChangedLine(old_line_number=None, new_line_number=new_line, kind=ChangedLineKind.ADDED, text=text)
            )
            if new_line is not None:
                new_line += 1
            continue

        if prefix == "-":
            current_hunk.changed_lines.append(
                ChangedLine(old_line_number=old_line, new_line_number=None, kind=ChangedLineKind.REMOVED, text=text)
            )
            if old_line is not None:
                old_line += 1
            continue

        current_hunk.changed_lines.append(
            ChangedLine(old_line_number=old_line, new_line_number=new_line, kind=ChangedLineKind.CONTEXT, text=text)
        )
        if old_line is not None:
            old_line += 1
        if new_line is not None:
            new_line += 1

    finish_file()
    return ParsedDiff(files=files)


def _parse_git_header(line: str) -> tuple[str | None, str | None]:
    parts = line.split()
    if len(parts) < 4:
        return None, None
    return _strip_ab_prefix(parts[2]), _strip_ab_prefix(parts[3])


def _normalize_marker_path(path: str) -> str | None:
    if path == "/dev/null":
        return None
    return _strip_ab_prefix(path.split("\t", 1)[0])


def _strip_ab_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _finalize_status(diff_file: DiffFile) -> None:
    if diff_file.is_binary:
        diff_file.status = "binary"
    elif diff_file.old_path is None and diff_file.new_path is not None:
        diff_file.status = "added"
    elif diff_file.old_path is not None and diff_file.new_path is None:
        diff_file.status = "deleted"
    elif diff_file.status != "renamed":
        diff_file.status = "modified"
