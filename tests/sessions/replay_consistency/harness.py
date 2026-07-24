# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pydantic data models for the replay consistency harness.

Defines the core data structures used throughout the framework:
- EventSpec: deterministic event template
- MemoryQuerySpec: memory search query with expected results
- SummaryPoint: summary checkpoint within a replay case
- ReplayCase: complete replay test scenario
- ReplaySnapshot: normalized backend state snapshot
- DiffEntry: structured cross-backend difference
- BackendStatus: per-backend availability status
- Report: top-level diff report
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


@dataclass(frozen=True)
class EventSpec:
    """Deterministic specification for constructing an Event.

    Each field maps directly to the Event model's constructor arguments.
    Using a frozen dataclass ensures replay cases are immutable and hashable.
    """

    invocation_id: str
    author: str
    role: str = "model"
    text: Optional[str] = None
    function_call: Optional[dict[str, Any]] = None
    function_response: Optional[dict[str, Any]] = None
    state_delta: Optional[dict[str, Any]] = None
    branch: Optional[str] = None
    tag: Optional[str] = None
    filter_key: Optional[str] = None
    partial: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    event_id: Optional[str] = None


@dataclass(frozen=True)
class MemoryQuerySpec:
    """Memory search query with expected result fragments."""

    key: Optional[str] = None
    query: str = ""
    limit: int = 10
    expected_text_fragments: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SummaryPoint:
    """A checkpoint within a replay case where a summary is created.

    summary_index: which event (0-indexed) triggers the summary creation
    description: human-readable label for this summary point
    """

    summary_index: int
    description: str = ""


@dataclass(frozen=True)
class ReplayCase:
    """A single replay test scenario.

    Encodes a complete session trajectory with events, memory queries,
    and summary checkpoints. All fields are deterministic to ensure
    identical replay across backends.
    """

    name: str
    app_name: str
    user_id: str
    session_id: str
    initial_state: dict[str, Any]
    events: list[EventSpec]
    memory_queries: list[MemoryQuerySpec]
    summary_points: list[int]  # event indices where summaries are created
    description: str = ""

    def __hash__(self) -> int:
        return hash(self.name)


class ReplaySnapshot(BaseModel):
    """Normalized representation of backend state after replay.

    Captures the complete observable state from a backend after executing
    a replay case. All volatile fields (timestamps, auto-generated IDs)
    are normalized before comparison.
    """

    model_config = ConfigDict(extra="forbid")

    case_name: str = ""
    backend: str = ""
    session_id: str = ""
    app_name: str = ""
    user_id: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)
    historical_events: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    memories: list[dict[str, Any]] = Field(default_factory=list)
    summary: Optional[dict[str, Any]] = None
    list_sessions: Optional[list[dict[str, Any]]] = None
    conversation_count: int = 0


class DiffEntry(BaseModel):
    """A single structured difference between two backend snapshots.

    Contains precise location information (session_id, event_index,
    summary_id, field_path) and the divergent values from both sides.
    """

    model_config = ConfigDict(extra="forbid")

    case_name: str = ""
    left_backend: str = ""
    right_backend: str = ""
    session_id: Optional[str] = None
    event_index: Optional[int] = None
    memory_index: Optional[int] = None
    summary_id: Optional[str] = None
    section: str = "root"
    path: str = ""
    left: Any = None
    right: Any = None
    allowed: bool = False
    reason: str = ""


class BackendStatus(BaseModel):
    """Availability status for a single backend."""

    name: str = ""
    status: str = "ok"  # ok | skipped | error
    reason: str = ""


class Report(BaseModel):
    """Top-level diff report generated after replay comparison.

    Schema version 3 adds: backend_statuses, false_positive_summary,
    mutation_summary, and report_kind discrimination.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 3
    report_kind: str = "normal_replay"  # normal_replay | mutation_replay
    generated_by: str = "tests/sessions/test_replay_consistency.py"
    generated_at: str = "deterministic"
    backend_statuses: list[BackendStatus] = Field(default_factory=list)
    backend_pairs: list[str] = Field(default_factory=list)
    case_count: int = 0
    cases: list[dict[str, Any]] = Field(default_factory=list)
    diffs: list[DiffEntry] = Field(default_factory=list)
    false_positive_summary: dict[str, Any] = Field(default_factory=dict)
    mutation_summary: dict[str, Any] = Field(default_factory=dict)
    allowed_diff_count: int = 0
    unallowed_diff_count: int = 0
    unexpected_diff_count: int = 0
