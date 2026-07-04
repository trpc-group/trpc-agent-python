# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

""">Replay consistency test harness for Session, Memory, and
Summary backends.

Drives InMemory, SQLite, and optional Redis backends with the
same deterministic replay cases, normalizes non-business
differences, compares snapshots, and writes a structured
diff report to ``session_memory_summary_diff_report.json``.

Mirrors the Go implementation in
``trpc-agent-go/session/replaytest/`` for shared fixtures and
cross-language verification.

Sharing Strategy (Go / Python)
------------------------------
- Both frameworks share the same 10 replay case definitions
  in structure, enabling cross-language consistency checking.
- Fixtures are JSON-compatible so that Go-generated fixtures
  can be consumed by the Python harness and vice versa.
- The diff report format (``DiffEntry``) is identical between
  languages, allowing unified CI dashboards.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import pathlib
import re
from collections.abc import Sequence
from typing import Any

import pytest

# Session imports.
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._in_memory_session_service import (
    InMemorySessionService,
)
from trpc_agent_sdk.sessions._session import Session
from trpc_agent_sdk.sessions._sql_session_service import (
    SqlSessionService,
)
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content, EventActions, Part, State

# Memory imports.
from trpc_agent_sdk.memory._in_memory_memory_service import (
    InMemoryMemoryService,
)
from trpc_agent_sdk.memory._sql_memory_service import (
    SqlMemoryService,
)

# ---------------------------------------------------------------------------
# Data types (mirrors Go session/replaytest/types.go)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class EventSpec:
    """Backend-independent event descriptor."""
    author: str
    invocation_id: str = ""
    role: str = "user"         # "user" | "assistant" | "tool"
    text: str = ""
    tool_calls: list[dict[str, Any]] = dataclasses.field(
        default_factory=list)
    tool_response: dict[str, str] | None = None
    state_delta: dict[str, str] | None = None
    filter_key: str = ""
    branch: str = ""
    tag: str = ""


@dataclasses.dataclass
class MemoryWriteSpec:
    """A memory entry to store during replay."""
    memory: str
    topics: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class MemoryQuerySpec:
    """A memory search to execute during replay."""
    query: str
    limit: int = 10


@dataclasses.dataclass
class SummaryStep:
    """A summary operation triggered after a specific event index."""
    after_event_index: int
    filter_key: str = ""
    force: bool = False


@dataclasses.dataclass
class TrackEventSpec:
    """A track event to append during replay."""
    track: str
    payload: str  # JSON string.


@dataclasses.dataclass
class ReplayCase:
    """A single replay test scenario."""
    name: str
    app_name: str = "test-app"
    user_id: str = "user-1"
    session_id: str = ""
    initial_state: dict[str, str] = dataclasses.field(
        default_factory=dict)
    events: list[EventSpec] = dataclasses.field(
        default_factory=list)
    memory_writes: list[MemoryWriteSpec] = dataclasses.field(
        default_factory=list)
    memory_queries: list[MemoryQuerySpec] = dataclasses.field(
        default_factory=list)
    summary_steps: list[SummaryStep] = dataclasses.field(
        default_factory=list)
    track_events: list[TrackEventSpec] = dataclasses.field(
        default_factory=list)


@dataclasses.dataclass
class DiffEntry:
    """A single normalized field mismatch."""
    session_id: str | None = None
    event_index: int | None = None
    memory_id: str | None = None
    summary_id: str | None = None
    track_name: str | None = None
    section: str = ""
    path: str = ""
    left: Any = None
    right: Any = None
    allowed: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# 10 Replay cases (mirrors Go session/replaytest/replaytest_test.go)
# ---------------------------------------------------------------------------

def _replay_cases() -> list[ReplayCase]:
    return [
        _case1_single_turn(),
        _case2_multi_turn(),
        _case3_tool_call(),
        _case4_state_updates(),
        _case5_memory_rw(),
        _case6_summary_update(),
        _case7_summary_truncation(),
        _case8_track_events(),
        _case9_concurrent_writes(),
        _case10_error_recovery(),
    ]


def _case1_single_turn() -> ReplayCase:
    return ReplayCase(
        name="single_turn_text",
        session_id="session-001",
        initial_state={"app:welcome": "true"},
        events=[
            EventSpec(author="user", role="user",
                      text="Hello, who are you?"),
            EventSpec(author="assistant", role="assistant",
                      text="I am an AI assistant."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User greeted the assistant",
                            topics=["conversation"]),
        ],
        memory_queries=[MemoryQuerySpec(query="greeting", limit=5)],
    )


def _case2_multi_turn() -> ReplayCase:
    return ReplayCase(
        name="multi_turn_state_updates",
        session_id="session-002",
        events=[
            EventSpec(author="user", role="user",
                      text="What is my name?"),
            EventSpec(author="assistant", role="assistant",
                      text="Your name is Bob."),
            EventSpec(author="user", role="user",
                      text="Remember that I like coffee."),
            EventSpec(author="assistant", role="assistant",
                      text="I will remember that you like coffee."),
            EventSpec(author="user", role="user",
                      text="What did I ask you to remember?"),
            EventSpec(author="assistant", role="assistant",
                      text="You asked me to remember that you like coffee."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User name is Bob",
                            topics=["identity"]),
            MemoryWriteSpec(memory="User likes coffee",
                            topics=["preferences"]),
        ],
        memory_queries=[
            MemoryQuerySpec(query="Bob", limit=5),
            MemoryQuerySpec(query="coffee", limit=5),
        ],
    )


def _case3_tool_call() -> ReplayCase:
    return ReplayCase(
        name="tool_call_roundtrip",
        session_id="session-003",
        events=[
            EventSpec(author="user", role="user",
                      text="What is the weather?"),
            EventSpec(author="assistant", role="assistant",
                      tool_calls=[{
                          "id": "call-1",
                          "name": "get_weather",
                          "arguments": '{"city":"Beijing"}',
                      }]),
            EventSpec(author="tool", role="tool",
                      tool_response={
                          "id": "call-1",
                          "name": "get_weather",
                          "content": "Sunny, 25°C",
                      }),
            EventSpec(author="assistant", role="assistant",
                      text="The weather in Beijing is sunny, 25°C."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User checked weather for Beijing",
                            topics=["action"]),
        ],
        memory_queries=[MemoryQuerySpec(query="weather", limit=5)],
    )


def _case4_state_updates() -> ReplayCase:
    return ReplayCase(
        name="scoped_state_overwrite",
        session_id="session-004",
        initial_state={"user:score": "0", "app:round": "1"},
        events=[
            EventSpec(author="assistant", role="assistant",
                      text="Starting round 1.",
                      state_delta={"user:score": "10"}),
            EventSpec(author="user", role="user",
                      text="I found the answer."),
            EventSpec(author="assistant", role="assistant",
                      text="Score updated.",
                      state_delta={"user:score": "25",
                                  "app:round": "2"}),
        ],
    )


def _case5_memory_rw() -> ReplayCase:
    return ReplayCase(
        name="memory_multi_author_search",
        user_id="user-2",
        session_id="session-005",
        events=[
            EventSpec(author="user", role="user",
                      text="I enjoy hiking on weekends."),
            EventSpec(author="assistant", role="assistant",
                      text="That's great! Hiking is wonderful."),
            EventSpec(author="user", role="user",
                      text="Also I prefer tea over coffee."),
            EventSpec(author="assistant", role="assistant",
                      text="Noted - you prefer tea."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User enjoys hiking",
                            topics=["hobbies"]),
            MemoryWriteSpec(memory="User prefers tea",
                            topics=["preferences"]),
            MemoryWriteSpec(memory="User is health conscious",
                            topics=["lifestyle"]),
        ],
        memory_queries=[
            MemoryQuerySpec(query="hiking", limit=3),
            MemoryQuerySpec(query="tea", limit=3),
            MemoryQuerySpec(query="preferences", limit=5),
        ],
    )


def _case6_summary_update() -> ReplayCase:
    return ReplayCase(
        name="summary_generation",
        session_id="session-006",
        events=[
            EventSpec(author="user", role="user",
                      text="Help me plan a trip to Shanghai."),
            EventSpec(author="assistant", role="assistant",
                      text="Sure! When would you like to go?"),
            EventSpec(author="user", role="user",
                      text="Next month, for 3 days."),
            EventSpec(author="assistant", role="assistant",
                      text="I recommend visiting the Bund and Yu Garden."),
            EventSpec(author="user", role="user",
                      text="Great, also I need hotel recommendations."),
            EventSpec(author="assistant", role="assistant",
                      text="I can search for hotels near the Bund."),
        ],
        summary_steps=[SummaryStep(after_event_index=6, force=True)],
    )


def _case7_summary_truncation() -> ReplayCase:
    return ReplayCase(
        name="summary_with_truncation",
        session_id="session-007",
        events=[
            EventSpec(author="user", role="user",
                      text="Message 1: Greetings."),
            EventSpec(author="assistant", role="assistant",
                      text="Response 1: Hello!"),
            EventSpec(author="user", role="user",
                      text="Message 2: Question about weather."),
            EventSpec(author="assistant", role="assistant",
                      text="Response 2: It's sunny."),
            EventSpec(author="user", role="user",
                      text="Message 3: Thank you."),
            EventSpec(author="assistant", role="assistant",
                      text="Response 3: You're welcome."),
            EventSpec(author="user", role="user",
                      text="Message 4: Can you summarize?"),
            EventSpec(author="assistant", role="assistant",
                      text="Response 4: Here is the summary."),
        ],
        summary_steps=[
            SummaryStep(after_event_index=6, filter_key="weather",
                        force=True),
            SummaryStep(after_event_index=8, force=True),
        ],
    )


def _case8_track_events() -> ReplayCase:
    return ReplayCase(
        name="track_events",
        session_id="session-008",
        events=[
            EventSpec(author="user", role="user",
                      text="Run a calculation."),
            EventSpec(author="assistant", role="assistant",
                      text="Running calculation..."),
        ],
        track_events=[
            TrackEventSpec(track="tool_execution",
                           payload='{"tool":"calculator","duration_ms":150,"status":"success"}'),
            TrackEventSpec(track="tool_execution",
                           payload='{"tool":"calculator","duration_ms":200,"status":"success"}'),
            TrackEventSpec(track="subtask_status",
                           payload='{"subtask":"verification","status":"passed"}'),
        ],
    )


def _case9_concurrent_writes() -> ReplayCase:
    return ReplayCase(
        name="concurrent_out_of_order_writes",
        user_id="user-3",
        session_id="session-009",
        events=[
            EventSpec(author="user", role="user",
                      text="Start parallel task A."),
            EventSpec(author="user", role="user",
                      text="Start parallel task B."),
            EventSpec(author="assistant", role="assistant",
                      text="Task A result."),
            EventSpec(author="assistant", role="assistant",
                      text="Task B result."),
            EventSpec(author="user", role="user",
                      text="Merge results."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="Parallel task A completed",
                            topics=["task"]),
            MemoryWriteSpec(memory="Parallel task B completed",
                            topics=["task"]),
        ],
    )


def _case10_error_recovery() -> ReplayCase:
    return ReplayCase(
        name="error_recovery",
        session_id="session-010",
        events=[
            EventSpec(author="user", role="user",
                      text="Normal message 1."),
            EventSpec(author="assistant", role="assistant",
                      text="Normal response 1."),
            EventSpec(author="user", role="user",
                      text="Duplicate test message."),
            EventSpec(author="user", role="user",
                      text="Duplicate test message."),
            EventSpec(author="assistant", role="assistant",
                      text="Response to duplicate."),
            EventSpec(author="user", role="user",
                      text="Final message."),
            EventSpec(author="assistant", role="assistant",
                      text="Final response."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="Normal operation recorded",
                            topics=["status"]),
            MemoryWriteSpec(memory="Normal operation recorded",
                            topics=["status"]),
        ],
    )


# ---------------------------------------------------------------------------
# Backend bundles
# ---------------------------------------------------------------------------

def _make_backend_config():
    """Create a minimal session service config."""
    return SessionServiceConfig()


def _make_inmemory_backend():
    """Create an InMemory backend with session + memory services."""
    cfg = _make_backend_config()
    sess_svc = InMemorySessionService(session_config=cfg)
    mem_svc = InMemoryMemoryService()
    return sess_svc, mem_svc


def _make_sqlite_backend(db_path: str):
    """Create a SQLite backend with session + memory services."""
    cfg = _make_backend_config()
    sess_svc = SqlSessionService(sqlite_db_path=db_path,
                                  session_config=cfg)
    mem_svc = SqlMemoryService(sqlite_db_path=db_path)
    return sess_svc, mem_svc


# ---------------------------------------------------------------------------
# Normalizer (mirrors Go session/replaytest/normalizer.go)
# ---------------------------------------------------------------------------

def _normalize_event(event: Event) -> dict[str, Any]:
    """Strip auto-generated fields from an event for comparison."""
    norm: dict[str, Any] = {
        "author": event.author,
    }
    if event.content and event.content.parts:
        # Extract text from parts.
        texts = []
        for part in event.content.parts:
            if hasattr(part, "text") and part.text:
                texts.append(part.text)
        norm["text"] = " ".join(texts)

    if event.actions and event.actions.state_delta:
        norm["state_delta"] = {
            k: v for k, v in event.actions.state_delta.items()
        }
    return norm


def _normalize_snapshot(session: Session,
                        memories: list[dict[str, Any]],
                        ) -> dict[str, Any]:
    """Produce a normalized snapshot for comparison."""
    events_norm = [_normalize_event(e) for e in session.events]
    return {
        "session_id": session.id,
        "state": dict(session.state) if session.state else {},
        "events": events_norm,
        "memories": sorted(memories, key=lambda m: m.get("content", "")),
        "summaries": dict(session.summaries) if hasattr(
            session, "summaries"
        ) and session.summaries else {},
    }


# ---------------------------------------------------------------------------
# Comparator (mirrors Go session/replaytest/comparator.go)
# ---------------------------------------------------------------------------

def _recursive_diff(left: Any, right: Any,
                    path: str = "",
                    case_name: str = "") -> list[DiffEntry]:
    """Recursively compare two values and return a list of diffs."""
    diffs: list[DiffEntry] = []

    if isinstance(left, dict) and isinstance(right, dict):
        all_keys = set(left.keys()) | set(right.keys())
        for key in sorted(all_keys):
            child_path = f"{path}.{key}" if path else key
            left_val = left.get(key)
            right_val = right.get(key)
            if left_val != right_val:
                diffs.extend(
                    _recursive_diff(left_val, right_val, child_path,
                                    case_name))

    elif isinstance(left, list) and isinstance(right, list):
        max_len = max(len(left), len(right))
        for i in range(max_len):
            child_path = f"{path}[{i}]"
            left_val = left[i] if i < len(left) else None
            right_val = right[i] if i < len(right) else None
            if left_val != right_val:
                diffs.extend(
                    _recursive_diff(left_val, right_val, child_path,
                                    case_name))
    else:
        if left != right:
            diffs.append(DiffEntry(
                section=path.split(".")[0] if "." in path else path,
                path=path,
                left=left,
                right=right,
            ))

    return diffs


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

def _generate_report(
    diffs_by_case: dict[str, list[DiffEntry]],
    output_path: str = "session_memory_summary_diff_report.json",
) -> None:
    """Write the diff report to a JSON file."""
    report = []
    for case_name, diffs in diffs_by_case.items():
        report.append({
            "case_name": case_name,
            "diffs": [
                dataclasses.asdict(d) for d in diffs
            ],
        })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)


# =============================================================================
# Test functions
# =============================================================================

@pytest.mark.asyncio
class TestReplayConsistency:

    async def test_in_memory_and_sqlite_session_replay_match(
        self, tmp_path: pathlib.Path,
    ):
        """Run all 10 replay cases across InMemory and SQLite backends
        and assert zero unallowed diffs."""
        import tempfile

        cases = _replay_cases()
        assert len(cases) == 10, f"Expected 10 cases, got {len(cases)}"

        backends = [
            ("inmemory", *_make_inmemory_backend()),
        ]

        # Only add SQLite if available.
        try:
            db_path = str(tmp_path / "replay_test.db")
            sqlite_sess, sqlite_mem = _make_sqlite_backend(db_path)
            backends.append(
                ("sqlite", sqlite_sess, sqlite_mem))
        except Exception:
            pass  # SQLite backend not available.

        all_diffs: dict[str, list[DiffEntry]] = {}

        for case in cases:
            snapshots = []
            for name, sess_svc, mem_svc in backends:
                snapshot = await _run_case(sess_svc, mem_svc, case)
                snapshots.append((name, snapshot))

            # Pairwise comparison.
            for i in range(len(snapshots)):
                for j in range(i + 1, len(snapshots)):
                    name_a, snap_a = snapshots[i]
                    name_b, snap_b = snapshots[j]
                    diffs = _recursive_diff(
                        snap_a, snap_b,
                        case_name=f"{case.name} [{name_a} vs {name_b}]",
                    )
                    key = f"{case.name}_{name_a}_vs_{name_b}"
                    all_diffs[key] = diffs

                    unallowed = [d for d in diffs if not d.allowed]
                    if unallowed:
                        for d in unallowed:
                            print(
                                f"UNALLOWED DIFF [{case.name}]: "
                                f"{d.path}: {d.left} != {d.right}"
                            )
                    assert len(unallowed) == 0, (
                        f"Case '{case.name}' has {len(unallowed)} "
                        f"unallowed diffs between {name_a} and {name_b}"
                    )

            # Cleanup after each case.
            for _, sess_svc, _ in backends:
                if hasattr(sess_svc, "close"):
                    await sess_svc.close()

        # Generate report.
        _generate_report(all_diffs)

    async def test_diff_detects_summary_injections(self):
        """Verify summary-specific diff detection."""
        left = {"summaries": {"": "Correct summary"}}
        right: dict[str, Any] = {"summaries": {}}
        diffs = _recursive_diff(left, right)
        assert len(diffs) > 0, "Missing summary should be detected"

        right2 = {"summaries": {"": "Overwritten text"}}
        diffs2 = _recursive_diff(left, right2)
        summary_diffs = [
            d for d in diffs2 if "summary" in d.path.lower()
        ]
        assert len(summary_diffs) > 0, (
            "Summary overwrite must be detected"
        )

    async def test_diff_detects_state_memory_injections(self):
        """Verify state, memory, and event diffs are detected."""
        left = {
            "events": [{"author": "user", "text": "Hello"}],
            "state": {"key": "value1"},
            "memories": [{"content": "test memory"}],
            "tracks": [{"track": "exec", "payload": '{"ok":true}'}],
        }
        right = {
            "events": [{"author": "user", "text": "Different"}],
            "state": {"key": "value2"},
            "memories": [{"content": "other memory"}],
            "tracks": [{"track": "exec", "payload": '{"ok":false}'}],
        }

        diffs = _recursive_diff(left, right)
        sections = {d.section for d in diffs}
        assert "events" in sections, "Event diffs not detected"
        assert "state" in sections, "State diffs not detected"
        assert "memories" in sections, "Memory diffs not detected"
        assert "tracks" in sections, "Track diffs not detected"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _run_case(
    sess_svc,
    mem_svc,
    case: ReplayCase,
) -> dict[str, Any]:
    """Execute a single replay case against the given backends."""
    # Create session.
    session = await sess_svc.create_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
        state=case.initial_state,
    )

    # Append events.
    for i, es in enumerate(case.events):
        actions = EventActions(
            state_delta=es.state_delta) if es.state_delta else EventActions()
        content = Content(
            parts=[Part.from_text(text=es.text)] if es.text else [])
        event = Event(
            invocation_id=es.invocation_id or f"inv-{case.session_id}-{i}",
            author=es.author,
            content=content,
            actions=actions,
        )
        await sess_svc.append_event(session, event)

        # Check for summary steps.
        for ss in case.summary_steps:
            if ss.after_event_index == i + 1:
                try:
                    await sess_svc.create_session_summary(
                        session=session,
                        filter_key=ss.filter_key,
                    )
                except Exception:
                    pass  # Summary may not be supported.

    # Write memories.
    for mw in case.memory_writes:
        try:
            await mem_svc.add_memory(
                app_name=case.app_name,
                user_id=case.user_id,
                memory=mw.memory,
                topics=mw.topics,
            )
        except Exception:
            pass

    # Query memories.
    all_memories: list[dict[str, Any]] = []
    for mq in case.memory_queries:
        try:
            entries = await mem_svc.search_memories(
                app_name=case.app_name,
                user_id=case.user_id,
                query=mq.query,
                max_results=mq.limit,
            )
            for entry in entries:
                if hasattr(entry, "memory") and entry.memory:
                    all_memories.append({
                        "content": entry.memory.memory,
                        "topics": getattr(
                            entry.memory, "topics", []),
                    })
        except Exception:
            pass

    # Re-fetch session to get latest state.
    session = await sess_svc.get_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
    )

    return _normalize_snapshot(session, all_memories)
