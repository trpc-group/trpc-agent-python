# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Data formats used by Advanced Memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from enum import Enum

_FRONTMATTER_PATTERN = re.compile(r"\A---\n(?P<frontmatter>.*?)\n---(?:\n|\Z)", re.DOTALL)
_UPDATED_AT_PATTERN = re.compile(r"^updated_at:\s*(?P<value>\S+)\s*$", re.MULTILINE)


def _as_utc(value: datetime) -> datetime:
    """Normalize an aware or naive datetime to UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MemoryType(str, Enum):
    """Semantic types allowed for long-term memory documents."""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass(frozen=True)
class MemoryIndexEntry:
    """Represent one entry in MEMORY.md."""

    name: str
    filename: str
    summary: str

    def __post_init__(self) -> None:
        """Validate that index fields are non-empty single-line strings."""
        for field_name, value in (
            ("name", self.name),
            ("filename", self.filename),
            ("summary", self.summary),
        ):
            if not value.strip() or "\n" in value or "\r" in value:
                raise ValueError(f"{field_name} must be non-empty single-line text")

    def to_markdown(self) -> str:
        """Render one standard index entry."""
        return f"- [{self.name.strip()}]（{self.filename.strip()}）:{self.summary.strip()}"


@dataclass(frozen=True)
class MemoryDocument:
    """Represent one long-term memory topic."""

    name: str
    description: str
    memory_type: MemoryType
    content: str
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate frontmatter fields."""
        for field_name, value in (
            ("name", self.name),
            ("description", self.description),
        ):
            if not value.strip() or "\n" in value or "\r" in value:
                raise ValueError(f"{field_name} must be non-empty single-line text")

    def to_markdown(self) -> str:
        """Render the topic as Markdown with frontmatter."""
        body = self.content.strip()
        updated_at = _as_utc(self.updated_at).isoformat() if self.updated_at is not None else None
        updated_at_line = f"updated_at: {updated_at}\n" if updated_at else ""
        return ("---\n"
                f"name: {self.name.strip()}\n"
                f"description: {self.description.strip()}\n"
                f"type: {self.memory_type.value}\n"
                f"{updated_at_line}"
                "---\n"
                f"{body}\n")


def parse_memory_updated_at(content: str) -> datetime | None:
    """Extract the UTC update timestamp from a memory document."""
    frontmatter_match = _FRONTMATTER_PATTERN.match(content)
    if frontmatter_match is None:
        return None
    match = _UPDATED_AT_PATTERN.search(frontmatter_match.group("frontmatter"))
    if match is None:
        return None
    try:
        parsed = datetime.fromisoformat(match.group("value").replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def memory_freshness(updated_at: datetime | None, *, now: datetime | None = None) -> str:
    """Return a compact freshness bucket for model-facing output."""
    if updated_at is None:
        return "unknown"
    age_days = max(0, int((_as_utc(now or datetime.now(timezone.utc)) - _as_utc(updated_at)).total_seconds()) // 86_400)
    if age_days == 0:
        return "today"
    if age_days == 1:
        return "yesterday"
    if age_days <= 7:
        return "within 7 days"
    if age_days <= 30:
        return "within 30 days"
    return "over 30 days"


def limit_memory_index(index: str, *, max_lines: int, max_bytes: int, encoding: str) -> str:
    """Return a bounded view of an index without modifying the stored index."""
    lines: list[str] = []
    used_bytes = 0
    for line in index.splitlines(keepends=True)[:max_lines]:
        size = len(line.encode(encoding))
        if used_bytes + size > max_bytes:
            break
        lines.append(line)
        used_bytes += size
    return "".join(lines)
