"""Minimal unified diff parser for deterministic review fixtures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<context>.*)$"
)


@dataclass(frozen=True)
class ChangedLine:
    """A line changed by a unified diff hunk."""

    file_path: str
    line_number: int
    content: str
    hunk_header: str
    change_type: str


@dataclass(frozen=True)
class ParsedDiff:
    """Parsed unified diff data used by the static rule scanner."""

    files: list[str]
    changed_lines: list[ChangedLine]


def _normalize_diff_path(raw: str) -> str:
    value = raw.strip()
    if not value or value == "/dev/null":
        return value
    path = value.split("\t", maxsplit=1)[0].split(" ", maxsplit=1)[0]
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _unique_in_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def parse_unified_diff(diff_text: str) -> ParsedDiff:
    """Parse file paths, hunk headers, and changed lines from unified diff text.

    The parser records added and removed lines, but phase-1 rules only scan
    additions. Context lines are used only to keep line counters accurate.
    """

    files: list[str] = []
    changed_lines: list[ChangedLine] = []

    current_file = ""
    pending_file = ""
    hunk_header = ""
    old_line = 0
    new_line = 0
    in_hunk = False

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            parts = raw_line.split()
            if len(parts) >= 4:
                pending_file = _normalize_diff_path(parts[3])
                current_file = pending_file
                files.append(current_file)
            in_hunk = False
            hunk_header = ""
            continue

        if raw_line.startswith("+++ "):
            path = _normalize_diff_path(raw_line[4:])
            if path != "/dev/null":
                current_file = path
                files.append(current_file)
            elif pending_file:
                current_file = pending_file
            continue

        match = _HUNK_RE.match(raw_line)
        if match:
            hunk_header = raw_line
            old_line = int(match.group("old_start"))
            new_line = int(match.group("new_start"))
            in_hunk = True
            continue

        if not in_hunk or not current_file:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            changed_lines.append(
                ChangedLine(
                    file_path=current_file,
                    line_number=new_line,
                    content=raw_line[1:],
                    hunk_header=hunk_header,
                    change_type="add",
                )
            )
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            changed_lines.append(
                ChangedLine(
                    file_path=current_file,
                    line_number=old_line,
                    content=raw_line[1:],
                    hunk_header=hunk_header,
                    change_type="delete",
                )
            )
            old_line += 1
        elif raw_line.startswith(" "):
            old_line += 1
            new_line += 1
        elif raw_line.startswith("\\"):
            continue
        else:
            in_hunk = False

    return ParsedDiff(files=_unique_in_order(files), changed_lines=changed_lines)
