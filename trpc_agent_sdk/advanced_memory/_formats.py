# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Define shared formats for long-term and session memory."""

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
    """Represent one standard entry in MEMORY.md."""

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
        """Render one long-term memory index entry."""
        return f"- [{self.name.strip()}]（{self.filename.strip()}）:{self.summary.strip()}"


@dataclass(frozen=True)
class MemoryDocument:
    """Represent a long-term memory document with frontmatter."""

    name: str
    description: str
    memory_type: MemoryType
    content: str
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate frontmatter and reject unsafe multiline values."""
        for field_name, value in (
            ("name", self.name),
            ("description", self.description),
        ):
            if not value.strip() or "\n" in value or "\r" in value:
                raise ValueError(f"{field_name} must be non-empty single-line text")

    def to_markdown(self) -> str:
        """Render standard frontmatter and document content."""
        body = self.content.strip()
        updated_at = (_as_utc(self.updated_at).isoformat() if self.updated_at is not None else None)
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
        value = match.group("value").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _as_utc(parsed)


def memory_freshness(updated_at: datetime | None, *, now: datetime | None = None) -> str:
    """Return a compact freshness bucket suitable for model-facing output."""
    if updated_at is None:
        return "unknown"
    current = _as_utc(now or datetime.now(timezone.utc))
    timestamp = _as_utc(updated_at)
    age_days = max(0, int((current - timestamp).total_seconds()) // 86_400)
    if age_days == 0:
        return "today"
    if age_days == 1:
        return "yesterday"
    if age_days <= 7:
        return "within 7 days"
    if age_days <= 30:
        return "within 30 days"
    return "over 30 days"


SESSION_MEMORY_SECTIONS = (
    "Session Title",
    "Current State",
    "Task specification",
    "Files and Functions",
    "Workflow",
    "Errors & Corrections",
    "Codebase and System Documentation",
    "Learnings",
    "Key results",
    "Worklog",
)

SESSION_MEMORY_SECTION_DESCRIPTIONS = (
    "A short and distinctive 5-10 word descriptive title for the session",
    "What is actively being worked on right now? Pending tasks not yet completed.",
    "What did the user ask to build? Any design decisions or other explanatory context",
    "What are the important files? In short, what do they contain?",
    "What bash commands are usually run and in what order?",
    "Errors encountered and how they were fixed. What approaches failed?",
    "What are the important system components? How do they work/fit together?",
    "What has worked well? What has not? What to avoid?",
    "If the user asked a specific output, repeat the exact result here",
    "Step by step, what was attempted, done? Very terse summary",
)


@dataclass(frozen=True)
class SessionMemoryDocument:
    """Represent structured session memory with ten fixed sections."""

    session_title: str = ""
    current_state: str = ""
    task_specification: str = ""
    files_and_functions: str = ""
    workflow: str = ""
    errors_and_corrections: str = ""
    codebase_and_system_documentation: str = ""
    learnings: str = ""
    key_results: str = ""
    worklog: str = ""

    def to_markdown(self) -> str:
        """Render all sections in fixed order, including empty sections."""
        values = (
            self.session_title,
            self.current_state,
            self.task_specification,
            self.files_and_functions,
            self.workflow,
            self.errors_and_corrections,
            self.codebase_and_system_documentation,
            self.learnings,
            self.key_results,
            self.worklog,
        )
        sections = [
            f"# {section}\n_{description}_\n\n{value.strip()}" for section, description, value in zip(
                SESSION_MEMORY_SECTIONS,
                SESSION_MEMORY_SECTION_DESCRIPTIONS,
                values,
            )
        ]
        return "\n\n".join(sections).rstrip() + "\n"
