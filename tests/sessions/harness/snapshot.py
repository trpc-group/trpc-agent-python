# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Snapshot data structures for capturing backend state after replay."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from trpc_agent_sdk.abc import MemoryEntry
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionSummary


@dataclass
class BackendSnapshot:
    """Full state snapshot of a single backend after executing all replay operations.

    Attributes:
        backend_name: Identifier for the backend (e.g. "inmemory", "sql", "redis").
        sessions: Mapping from session_id to the Session object.
        memory_entries: Mapping from search key to list of MemoryEntry results.
        summaries: Mapping from session_id to SessionSummary.
        errors: Any errors encountered during playback (op_index, error_message).
    """

    backend_name: str
    sessions: dict[str, Session] = field(default_factory=dict)
    memory_entries: dict[str, list[MemoryEntry]] = field(default_factory=dict)
    summaries: dict[str, SessionSummary] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=dict)

    def to_serializable(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary for diff reporting."""
        return {
            "backend_name": self.backend_name,
            "session_ids": list(self.sessions.keys()),
            "session_event_counts": {
                sid: len(s.events) for sid, s in self.sessions.items()
            },
            "memory_keys": list(self.memory_entries.keys()),
            "memory_entry_counts": {
                k: len(v) for k, v in self.memory_entries.items()
            },
            "summary_session_ids": list(self.summaries.keys()),
            "error_count": len(self.errors),
            "errors": self.errors,
        }