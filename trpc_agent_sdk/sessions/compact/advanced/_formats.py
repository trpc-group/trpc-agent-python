# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Define shared formats for long-term and session memory."""

# flake8: noqa: E125

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import fields

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
SESSION_MEMORY_STATE_KEY = "_trpc_agent:summary"
SESSION_MEMORY_STATE_SCHEMA_VERSION = 1

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


def build_session_memory_state(
    document: SessionMemoryDocument,
    *,
    checkpoint: dict[str, object],
    context_tokens: int | None,
) -> dict[str, object]:
    """Build the versioned Session.state payload used by Redis and SQL."""
    return {
        "schema_version": SESSION_MEMORY_STATE_SCHEMA_VERSION,
        "document": asdict(document),
        "checkpoint": checkpoint,
        "metrics": {
            "session_memory_chars": len(document.to_markdown()),
            "context_tokens": context_tokens,
        },
    }


def parse_session_memory_state(
    value: object, ) -> tuple[SessionMemoryDocument, dict[str, object], dict[str, object]] | None:
    """Parse a persisted Session Memory state value."""
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != SESSION_MEMORY_STATE_SCHEMA_VERSION:
        return None
    raw_document = value.get("document")
    raw_checkpoint = value.get("checkpoint")
    raw_metrics = value.get("metrics", {})
    if not isinstance(raw_document, dict) or not isinstance(raw_checkpoint, dict):
        return None
    if (not isinstance(raw_checkpoint.get("last_event_id"), str)
            or not isinstance(raw_checkpoint.get("boundary_signature"), str)
            or not isinstance(raw_checkpoint.get("boundary_occurrence"), int)):
        return None
    if not isinstance(raw_metrics, dict):
        raw_metrics = {}
    allowed = {field.name for field in fields(SessionMemoryDocument)}
    if (any(key not in allowed for key in raw_document)
            or any(not isinstance(item, str) for item in raw_document.values())):
        return None
    try:
        document = SessionMemoryDocument(**{
            key: item
            for key, item in raw_document.items() if key in allowed and isinstance(item, str)
        })
    except TypeError:
        return None
    return document, dict(raw_checkpoint), dict(raw_metrics)
