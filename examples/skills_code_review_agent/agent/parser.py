"""Small unified-diff parser that preserves changed-file line numbers."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ChangedLine:
    file: str
    line: int
    content: str


@dataclass(frozen=True)
class DiffHunk:
    file: str
    header: str
    context: list[str]


@dataclass(frozen=True)
class ParsedDiff:
    changed_lines: list[ChangedLine]
    hunks: list[DiffHunk]


def parse_unified_diff_with_hunks(diff: str) -> ParsedDiff:
    """Preserve hunk headers and unchanged context alongside candidate lines."""
    changed = parse_unified_diff(diff)
    hunks: list[DiffHunk] = []
    file_name = ""
    header = ""
    context: list[str] = []
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            file_name = raw[6:]
        elif raw.startswith("@@"):
            if header:
                hunks.append(DiffHunk(file_name, header, context))
            header, context = raw, []
        elif header and raw.startswith(" "):
            context.append(raw[1:])
    if header:
        hunks.append(DiffHunk(file_name, header, context))
    return ParsedDiff(changed, hunks)


def parse_unified_diff(diff: str) -> list[ChangedLine]:
    lines: list[ChangedLine] = []
    file_name = ""
    new_line = 0
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ b/"):
            file_name = raw_line[6:]
        elif raw_line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,\d+)?", raw_line)
            new_line = int(match.group(1)) if match else 0
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            lines.append(ChangedLine(file_name, new_line, raw_line[1:]))
            new_line += 1
        elif raw_line.startswith("\\ No newline at end of file"):
            continue
        elif raw_line.startswith(" ") or (raw_line and not raw_line.startswith("-")):
            new_line += 1
    return lines
