"""Normalize unified diffs and the host agent's ReviewInput JSON."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class ChangedLine:
    number: int
    content: str


@dataclass
class ChangedFile:
    path: str
    changes: list[ChangedLine] = field(default_factory=list)


def _path(raw: str) -> str:
    value = raw.split("\t", 1)[0].strip()
    if value == "/dev/null":
        return value
    if value.startswith(("a/", "b/")):
        value = value[2:]
    if value.startswith("/") or ".." in value.split("/"):
        raise ValueError(f"unsafe diff path: {raw}")
    return value


def parse_diff(diff: str) -> list[ChangedFile]:
    """Return only files and added lines from a unified diff."""
    files: list[ChangedFile] = []
    current: ChangedFile | None = None
    new_line: int | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = _path(raw[4:])
            current = None if path == "/dev/null" else ChangedFile(path)
            if current:
                files.append(current)
        elif raw.startswith("@@ "):
            match = _HUNK.match(raw)
            if not match or current is None:
                raise ValueError(f"malformed diff hunk: {raw}")
            new_line = int(match.group(1))
        elif current is not None and new_line is not None:
            if raw.startswith("+") and not raw.startswith("+++"):
                current.changes.append(ChangedLine(new_line, raw[1:]))
                new_line += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                continue
            elif not raw.startswith("\\"):
                new_line += 1
    return files


def parse_review_input(data: dict[str, Any]) -> list[ChangedFile]:
    """Read the normalized payload produced by the host agent."""
    result = []
    for item in data.get("files", []):
        path = str(item.get("path", ""))
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"unsafe review path: {path}")
        changes = [
            ChangedLine(int(line["number"]), str(line["content"]))
            for line in item.get("candidate_lines", [])
        ]
        result.append(ChangedFile(path, changes))
    return result
