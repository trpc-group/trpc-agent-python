#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Standard replay traces for session, memory, and summary consistency tests."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Iterable
from typing import Mapping

APP_NAME = "replay-app"
USER_ID = "replay-user"
SESSION_ID = "replay-session"
BASE_TIMESTAMP = 1_700_000_000.0
TIMESTAMP_STEP_SECONDS = 1.0
LARGE_INTEGER_VALUE = 9_007_199_254_740_993


class OperationKind(str, Enum):
    """Supported replay operation kinds."""

    CREATE = "create"
    APPEND = "append"
    STORE_MEMORY = "store_memory"
    SEARCH_MEMORY = "search_memory"
    SUMMARY = "summary"
    UNKNOWN_OUTCOME_RETRY = "unknown_outcome_retry"
    UNKNOWN_MEMORY_RETRY = "unknown_memory_retry"
    UNKNOWN_SUMMARY_RETRY = "unknown_summary_retry"
    BEFORE_CALL_FAILURE = "before_call_failure"


@dataclass(frozen=True)
class ReplayOperation:
    """One deterministic operation in a replay trace."""

    kind: OperationKind
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayCase:
    """Named replay trace."""

    case_id: str
    operations: tuple[ReplayOperation, ...]
    expected: "ExpectedOutcome"


@dataclass(frozen=True)
class ExpectedOutcome:
    """Independent minimum business outcome for one trace."""

    event_ids: tuple[str, ...]
    state: Mapping[str, Any] = field(default_factory=dict)
    memory_counts: Mapping[str, int] = field(default_factory=dict)
    summary_generation: int = 0
    summary_fact: str = ""
    minimum_historical_events: int = 0
    failure_count: int = 0


def validate_replay_cases(cases: Iterable[ReplayCase]) -> None:
    """Reject ambiguous case identifiers before matrix execution."""
    case_ids = [case.case_id for case in cases]
    duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate replay case ids: {duplicates}")


def _create(state: Mapping[str, Any] | None = None) -> ReplayOperation:
    return ReplayOperation(OperationKind.CREATE, {"state": dict(state or {})})


def _text(event_id: str, author: str, text: str, *, state_delta: Mapping[str, Any] | None = None) -> ReplayOperation:
    payload = {
        "event_id": event_id,
        "author": author,
        "text": text,
    }
    if state_delta:
        payload["state_delta"] = dict(state_delta)
    return ReplayOperation(OperationKind.APPEND, payload)


def _tool(event_id: str, part_type: str, value: Mapping[str, Any]) -> ReplayOperation:
    return ReplayOperation(
        OperationKind.APPEND,
        {
            "event_id": event_id,
            "author": "agent" if part_type == "function_call" else "tool",
            "part_type": part_type,
            "value": dict(value),
        },
    )


def _summary() -> ReplayOperation:
    return ReplayOperation(OperationKind.SUMMARY)


def _memory(query: str) -> tuple[ReplayOperation, ReplayOperation]:
    return (
        ReplayOperation(OperationKind.STORE_MEMORY),
        ReplayOperation(OperationKind.SEARCH_MEMORY, {"query": query}),
    )


REPLAY_CASES = (
    ReplayCase(
        "single_turn",
        (
            _create(),
            _text("single-user", "user", "Hello replay"),
            _text("single-agent", "agent", "Hello user"),
        ),
        ExpectedOutcome(("single-user", "single-agent")),
    ),
    ReplayCase(
        "multi_turn",
        (
            _create(),
            _text("multi-u1", "user", "First question"),
            _text("multi-a1", "agent", "First answer"),
            _text("multi-u2", "user", "Second question"),
            _text("multi-a2", "agent", "Second answer"),
            _text("multi-u3", "user", "Third question"),
            _text("multi-a3", "agent", "Third answer"),
        ),
        ExpectedOutcome(("multi-u1", "multi-a1", "multi-u2", "multi-a2", "multi-u3", "multi-a3")),
    ),
    ReplayCase(
        "tool_round_trip",
        (
            _create(),
            _text("tool-user", "user", "Find weather"),
            _tool("tool-call", "function_call", {
                "name": "weather",
                "id": "call-weather",
                "args": {
                    "city": "Shenzhen"
                },
            }),
            _tool("tool-response", "function_response", {
                "name": "weather",
                "id": "call-weather",
                "response": {
                    "temperature": 28
                },
            }),
            _text("tool-agent", "agent", "It is 28 C"),
        ),
        ExpectedOutcome(("tool-user", "tool-call", "tool-response", "tool-agent")),
    ),
    ReplayCase(
        "state_overwrite",
        (
            _create({
                "theme": "light",
                "app:region": "cn",
                "user:language": "en",
                "large_counter": LARGE_INTEGER_VALUE,
            }),
            _text(
                "state-1",
                "agent",
                "state one",
                state_delta={
                    "theme": "dark",
                    "app:region": "apac",
                    "user:language": "zh",
                    "temp:request_id": "ephemeral",
                },
            ),
            _text("state-2", "agent", "state two", state_delta={"theme": "system"}),
        ),
        ExpectedOutcome(
            ("state-1", "state-2"),
            {
                "theme": "system",
                "app:region": "apac",
                "user:language": "zh",
                "large_counter": LARGE_INTEGER_VALUE,
            },
        ),
    ),
    ReplayCase(
        "memory_preference",
        (
            _create(),
            _text("memory-pref", "user", "I prefer jasmine tea"),
            _text("memory-fact", "agent", "Favorite drink is jasmine tea"),
            *_memory("jasmine"),
        ),
        ExpectedOutcome(("memory-pref", "memory-fact"), memory_counts={"jasmine": 2}),
    ),
    ReplayCase(
        "summary_create",
        (
            _create(),
            _text("summary-u1", "user", "Plan a trip"),
            _text("summary-a1", "agent", "Choose Shenzhen"),
            _text("summary-u2", "user", "Use the train"),
            _summary(),
        ),
        ExpectedOutcome(
            ("summary-a1", "summary-u2"),
            summary_generation=1,
            summary_fact="Plan a trip",
            minimum_historical_events=1,
        ),
    ),
    ReplayCase(
        "summary_update",
        (
            _create(),
            _text("update-u1", "user", "Initial requirement"),
            _text("update-a1", "agent", "Initial decision"),
            _text("update-u2", "user", "Keep details"),
            _summary(),
            _text("update-u3", "user", "New requirement"),
            _text("update-a2", "agent", "Updated decision"),
            _summary(),
        ),
        ExpectedOutcome(
            ("update-u3", "update-a2"),
            summary_generation=2,
            summary_fact="Initial requirement",
            minimum_historical_events=3,
        ),
    ),
    ReplayCase(
        "summary_truncation",
        (
            _create(),
            _text("truncate-u1", "user", "Old context"),
            _text("truncate-a1", "agent", "Old answer"),
            _text("truncate-u2", "user", "Recent context"),
            _text("truncate-a2", "agent", "Recent answer"),
            _summary(),
            _text("truncate-u3", "user", "Follow-up"),
            _text("truncate-a3", "agent", "Follow-up answer"),
        ),
        ExpectedOutcome(
            ("truncate-u2", "truncate-a2", "truncate-u3", "truncate-a3"),
            summary_generation=1,
            summary_fact="Old context",
            minimum_historical_events=2,
        ),
    ),
    ReplayCase(
        "duplicate_retry",
        (
            _create(),
            ReplayOperation(
                OperationKind.UNKNOWN_OUTCOME_RETRY,
                {
                    "event_id": "retry-event",
                    "author": "user",
                    "text": "Write exactly once",
                },
            ),
            ReplayOperation(OperationKind.UNKNOWN_MEMORY_RETRY),
            ReplayOperation(OperationKind.UNKNOWN_SUMMARY_RETRY),
        ),
        ExpectedOutcome(tuple(), summary_generation=1, summary_fact="Write exactly once"),
    ),
    ReplayCase(
        "write_recovery",
        (
            _create({"status": "clean"}),
            ReplayOperation(
                OperationKind.BEFORE_CALL_FAILURE,
                {
                    "event_id": "failed-event",
                    "author": "agent",
                    "text": "must not persist",
                    "state_delta": {
                        "status": "dirty"
                    },
                },
            ),
            _text("recovery-event", "agent", "recovered", state_delta={"status": "recovered"}),
            *_memory("recovered"),
        ),
        ExpectedOutcome(
            ("recovery-event", ),
            {"status": "recovered"},
            {"recovered": 1},
            failure_count=1,
        ),
    ),
)

REPLAY_CASE_BY_ID = {case.case_id: case for case in REPLAY_CASES}
