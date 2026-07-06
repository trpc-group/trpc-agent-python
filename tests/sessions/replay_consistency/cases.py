# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Replay case definitions and JSONL fixture load/save.

Defines 10 standardized replay scenarios that exercise session,
memory, summary, and track event features across backends.

Mirrors the Go implementation in trpc-agent-go/session/replaytest/.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any


# ---------------------------------------------------------------------------
# Data types (mirrors Go session/replaytest/types.go)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class EventSpec:
    """Backend-independent event descriptor."""
    author: str
    invocation_id: str = ""
    role: str = "user"
    text: str = ""
    tool_calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)
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
    initial_state: dict[str, str] = dataclasses.field(default_factory=dict)
    events: list[EventSpec] = dataclasses.field(default_factory=list)
    memory_writes: list[MemoryWriteSpec] = dataclasses.field(default_factory=list)
    memory_queries: list[MemoryQuerySpec] = dataclasses.field(default_factory=list)
    summary_steps: list[SummaryStep] = dataclasses.field(default_factory=list)
    track_events: list[TrackEventSpec] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# 10 Replay cases
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
            EventSpec(author="user", role="user", text="Hello, who are you?"),
            EventSpec(author="assistant", role="assistant", text="I am an AI assistant."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User greeted the assistant", topics=["conversation"]),
        ],
        memory_queries=[MemoryQuerySpec(query="greeting", limit=5)],
    )


def _case2_multi_turn() -> ReplayCase:
    return ReplayCase(
        name="multi_turn_state_updates",
        session_id="session-002",
        events=[
            EventSpec(author="user", role="user", text="What is my name?"),
            EventSpec(author="assistant", role="assistant", text="Your name is Bob."),
            EventSpec(author="user", role="user", text="Remember that I like coffee."),
            EventSpec(author="assistant", role="assistant", text="I will remember that you like coffee."),
            EventSpec(author="user", role="user", text="What did I ask you to remember?"),
            EventSpec(author="assistant", role="assistant", text="You asked me to remember that you like coffee."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User name is Bob", topics=["identity"]),
            MemoryWriteSpec(memory="User likes coffee", topics=["preferences"]),
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
            EventSpec(author="user", role="user", text="What is the weather?"),
            EventSpec(author="assistant", role="assistant", tool_calls=[{
                "id": "call-1", "name": "get_weather", "arguments": '{"city":"Beijing"}',
            }]),
            EventSpec(author="tool", role="tool", tool_response={
                "id": "call-1", "name": "get_weather", "content": "Sunny, 25°C",
            }),
            EventSpec(author="assistant", role="assistant",
                      text="The weather in Beijing is sunny, 25°C."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User checked weather for Beijing", topics=["action"]),
        ],
        memory_queries=[MemoryQuerySpec(query="weather", limit=5)],
    )


def _case4_state_updates() -> ReplayCase:
    return ReplayCase(
        name="scoped_state_overwrite",
        session_id="session-004",
        initial_state={"user:score": "0", "app:round": "1"},
        events=[
            EventSpec(author="assistant", role="assistant", text="Starting round 1.",
                      state_delta={"user:score": "10"}),
            EventSpec(author="user", role="user", text="I found the answer."),
            EventSpec(author="assistant", role="assistant", text="Score updated.",
                      state_delta={"user:score": "25", "app:round": "2"}),
        ],
    )


def _case5_memory_rw() -> ReplayCase:
    return ReplayCase(
        name="memory_multi_author_search",
        user_id="user-2",
        session_id="session-005",
        events=[
            EventSpec(author="user", role="user", text="I enjoy hiking on weekends."),
            EventSpec(author="assistant", role="assistant", text="That's great! Hiking is wonderful."),
            EventSpec(author="user", role="user", text="Also I prefer tea over coffee."),
            EventSpec(author="assistant", role="assistant", text="Noted - you prefer tea."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="User enjoys hiking", topics=["hobbies"]),
            MemoryWriteSpec(memory="User prefers tea", topics=["preferences"]),
            MemoryWriteSpec(memory="User is health conscious", topics=["lifestyle"]),
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
            EventSpec(author="user", role="user", text="Help me plan a trip to Shanghai."),
            EventSpec(author="assistant", role="assistant", text="Sure! When would you like to go?"),
            EventSpec(author="user", role="user", text="Next month, for 3 days."),
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
            EventSpec(author="user", role="user", text="Message 1: Greetings."),
            EventSpec(author="assistant", role="assistant", text="Response 1: Hello!"),
            EventSpec(author="user", role="user", text="Message 2: Question about weather."),
            EventSpec(author="assistant", role="assistant", text="Response 2: It's sunny."),
            EventSpec(author="user", role="user", text="Message 3: Thank you."),
            EventSpec(author="assistant", role="assistant", text="Response 3: You're welcome."),
            EventSpec(author="user", role="user", text="Message 4: Can you summarize?"),
            EventSpec(author="assistant", role="assistant",
                      text="Response 4: Here is the summary."),
        ],
        summary_steps=[
            SummaryStep(after_event_index=6, filter_key="weather", force=True),
            SummaryStep(after_event_index=8, force=True),
        ],
    )


def _case8_track_events() -> ReplayCase:
    return ReplayCase(
        name="track_events",
        session_id="session-008",
        events=[
            EventSpec(author="user", role="user", text="Run a calculation."),
            EventSpec(author="assistant", role="assistant", text="Running calculation..."),
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
            EventSpec(author="user", role="user", text="Start parallel task A."),
            EventSpec(author="user", role="user", text="Start parallel task B."),
            EventSpec(author="assistant", role="assistant", text="Task A result."),
            EventSpec(author="assistant", role="assistant", text="Task B result."),
            EventSpec(author="user", role="user", text="Merge results."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="Parallel task A completed", topics=["task"]),
            MemoryWriteSpec(memory="Parallel task B completed", topics=["task"]),
        ],
    )


def _case10_error_recovery() -> ReplayCase:
    return ReplayCase(
        name="error_recovery",
        session_id="session-010",
        events=[
            EventSpec(author="user", role="user", text="Normal message 1."),
            EventSpec(author="assistant", role="assistant", text="Normal response 1."),
            EventSpec(author="user", role="user", text="Duplicate test message."),
            EventSpec(author="user", role="user", text="Duplicate test message."),
            EventSpec(author="assistant", role="assistant", text="Response to duplicate."),
            EventSpec(author="user", role="user", text="Final message."),
            EventSpec(author="assistant", role="assistant", text="Final response."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="Normal operation recorded", topics=["status"]),
            MemoryWriteSpec(memory="Normal operation recorded", topics=["status"]),
        ],
    )


# ---------------------------------------------------------------------------
# JSONL fixture load/save
# ---------------------------------------------------------------------------

def _dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert dataclass instance to dict."""
    if dataclasses.is_dataclass(obj):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name))
                for f in dataclasses.fields(obj)}
    if isinstance(obj, list):
        return [_dataclass_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def save_case_to_jsonl(case: ReplayCase, path: str) -> None:
    """Save a replay case as a JSONL fixture file.

    Each step (event, memory_write, memory_query, summary_step, track_event)
    is written as one JSON line.
    """
    lines: list[dict[str, Any]] = []

    # Header: case metadata.
    header = {
        "type": "case_header",
        "name": case.name,
        "app_name": case.app_name,
        "user_id": case.user_id,
        "session_id": case.session_id,
        "initial_state": case.initial_state,
    }
    if dataclasses.is_dataclass(header):
        header = _dataclass_to_dict(header)
    lines.append(header)

    # Events.
    for es in case.events:
        lines.append({"type": "event"} | _dataclass_to_dict(es))

    # Memory writes.
    for mw in case.memory_writes:
        lines.append({"type": "memory_write"} | _dataclass_to_dict(mw))

    # Memory queries.
    for mq in case.memory_queries:
        lines.append({"type": "memory_query"} | _dataclass_to_dict(mq))

    # Summary steps.
    for ss in case.summary_steps:
        lines.append({"type": "summary_step"} | _dataclass_to_dict(ss))

    # Track events.
    for te in case.track_events:
        lines.append({"type": "track_event"} | _dataclass_to_dict(te))

    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def load_case_from_jsonl(path: str) -> ReplayCase:
    """Load a replay case from a JSONL fixture file."""
    events: list[EventSpec] = []
    memory_writes: list[MemoryWriteSpec] = []
    memory_queries: list[MemoryQuerySpec] = []
    summary_steps: list[SummaryStep] = []
    track_events: list[TrackEventSpec] = []

    name = ""
    app_name = "test-app"
    user_id = "user-1"
    session_id = ""
    initial_state: dict[str, str] = {}

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            typ = obj.pop("type", "")

            if typ == "case_header":
                name = obj.get("name", name)
                app_name = obj.get("app_name", app_name)
                user_id = obj.get("user_id", user_id)
                session_id = obj.get("session_id", session_id)
                initial_state = obj.get("initial_state", initial_state)
            elif typ == "event":
                events.append(EventSpec(**obj))
            elif typ == "memory_write":
                memory_writes.append(MemoryWriteSpec(**obj))
            elif typ == "memory_query":
                memory_queries.append(MemoryQuerySpec(**obj))
            elif typ == "summary_step":
                summary_steps.append(SummaryStep(**obj))
            elif typ == "track_event":
                track_events.append(TrackEventSpec(**obj))

    return ReplayCase(
        name=name,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        initial_state=initial_state,
        events=events,
        memory_writes=memory_writes,
        memory_queries=memory_queries,
        summary_steps=summary_steps,
        track_events=track_events,
    )
