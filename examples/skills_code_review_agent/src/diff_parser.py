"""Unified diff parsing helpers for the skills code review example."""

from __future__ import annotations

import re

from .review_types import ChangedFile, DiffHunk, DiffLine, DiffLineType, ParsedDiff

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$"
)


def parse_unified_diff(diff_text: str) -> ParsedDiff:
    """Parse unified diff text into structured file and hunk objects."""

    parsed = ParsedDiff(raw_diff=diff_text)
    current_file: ChangedFile | None = None
    current_hunk: DiffHunk | None = None
    pending_old_path: str | None = None
    old_line_no: int | None = None
    new_line_no: int | None = None

    def flush_hunk() -> None:
        nonlocal current_hunk
        if current_file is not None and current_hunk is not None:
            current_file.hunks.append(current_hunk)
        current_hunk = None

    def flush_file() -> None:
        nonlocal current_file
        flush_hunk()
        if current_file is not None:
            parsed.files.append(current_file)
        current_file = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            flush_file()
            old_path, new_path = _parse_diff_git_header(raw_line)
            current_file = ChangedFile(old_path=old_path, new_path=new_path)
            pending_old_path = None
            old_line_no = None
            new_line_no = None
            continue

        if raw_line.startswith("--- "):
            candidate_old_path = _normalize_diff_path(raw_line[4:])
            if current_file is None:
                pending_old_path = candidate_old_path
            else:
                current_file.old_path = candidate_old_path
            continue

        if raw_line.startswith("+++ "):
            candidate_new_path = _normalize_diff_path(raw_line[4:])
            if current_file is None:
                current_file = ChangedFile(
                    old_path=pending_old_path or "/dev/null",
                    new_path=candidate_new_path,
                )
            else:
                current_file.new_path = candidate_new_path
            pending_old_path = None
            continue

        if current_file is None:
            continue

        if raw_line.startswith("new file mode "):
            current_file.is_new_file = True
            continue

        if raw_line.startswith("deleted file mode "):
            current_file.is_deleted_file = True
            continue

        if raw_line.startswith("rename from "):
            current_file.is_rename = True
            current_file.old_path = raw_line.removeprefix("rename from ")
            continue

        if raw_line.startswith("rename to "):
            current_file.is_rename = True
            current_file.new_path = raw_line.removeprefix("rename to ")
            continue

        match = _HUNK_HEADER_RE.match(raw_line)
        if match:
            flush_hunk()
            current_hunk = DiffHunk(
                header=raw_line,
                old_start=int(match.group("old_start")),
                old_count=int(match.group("old_count") or "1"),
                new_start=int(match.group("new_start")),
                new_count=int(match.group("new_count") or "1"),
            )
            old_line_no = current_hunk.old_start
            new_line_no = current_hunk.new_start
            continue

        if current_hunk is None or raw_line == r"\ No newline at end of file":
            continue

        line = _parse_hunk_line(
            raw_line=raw_line,
            old_line_no=old_line_no,
            new_line_no=new_line_no,
        )
        if line is None:
            continue

        current_hunk.lines.append(line)
        if line.line_type == DiffLineType.CONTEXT:
            old_line_no = _increment(old_line_no)
            new_line_no = _increment(new_line_no)
        elif line.line_type == DiffLineType.ADD:
            new_line_no = _increment(new_line_no)
        else:
            old_line_no = _increment(old_line_no)

    flush_file()
    return parsed


def _parse_diff_git_header(raw_line: str) -> tuple[str, str]:
    """Parse `diff --git a/foo b/foo` into normalized paths."""

    parts = raw_line.split(maxsplit=3)
    if len(parts) != 4:
        raise ValueError(f"invalid diff git header: {raw_line}")
    return _normalize_diff_path(parts[2]), _normalize_diff_path(parts[3])


def _normalize_diff_path(path_text: str) -> str:
    """Normalize git diff paths like `a/foo.py` into `foo.py`."""

    if path_text == "/dev/null":
        return path_text

    if path_text.startswith("a/") or path_text.startswith("b/"):
        return path_text[2:]
    return path_text


def _parse_hunk_line(
    *,
    raw_line: str,
    old_line_no: int | None,
    new_line_no: int | None,
) -> DiffLine | None:
    """Parse a single hunk line."""

    if not raw_line:
        return None

    marker = raw_line[0]
    text = raw_line[1:]
    if marker == " ":
        return DiffLine(
            line_type=DiffLineType.CONTEXT,
            text=text,
            raw_line=raw_line,
            old_line_no=old_line_no,
            new_line_no=new_line_no,
        )
    if marker == "+":
        return DiffLine(
            line_type=DiffLineType.ADD,
            text=text,
            raw_line=raw_line,
            old_line_no=None,
            new_line_no=new_line_no,
        )
    if marker == "-":
        return DiffLine(
            line_type=DiffLineType.DELETE,
            text=text,
            raw_line=raw_line,
            old_line_no=old_line_no,
            new_line_no=None,
        )
    return None


def _increment(value: int | None) -> int | None:
    """Increment a line number when it exists."""

    if value is None:
        return None
    return value + 1
