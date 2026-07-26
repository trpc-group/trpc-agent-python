# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standard input traces for Session/Memory/Summary replay tests."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass(frozen=True)
class ReplayOperation:
    """One backend-independent operation in a replay trace."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayCase:
    """A complete trace and the memory queries evaluated afterwards."""

    case_id: str
    description: str
    operations: tuple[ReplayOperation, ...]
    memory_queries: tuple[str, ...] = ()


def _text(author: str, text: str, **extra: Any) -> ReplayOperation:
    return ReplayOperation("event", {"event_type": "text", "author": author, "text": text, **extra})


def _state(delta: dict[str, Any]) -> ReplayOperation:
    return ReplayOperation("event", {"event_type": "state", "author": "agent", "state_delta": delta})


def _dialogue(prefix: str, turns: int) -> tuple[ReplayOperation, ...]:
    operations: list[ReplayOperation] = []
    for turn in range(1, turns + 1):
        operations.extend((_text("user",
                                 f"{prefix} user turn {turn}"), _text("assistant", f"{prefix} assistant turn {turn}")))
    return tuple(operations)


REPLAY_CASES: tuple[ReplayCase, ...] = (
    ReplayCase(
        case_id="single_turn",
        description="single user/assistant exchange",
        operations=(_text("user", "hello replay"), _text("assistant", "hello user")),
    ),
    ReplayCase(
        case_id="multi_turn",
        description="three consecutive conversation turns",
        operations=_dialogue("multi", 3),
    ),
    ReplayCase(
        case_id="tool_call",
        description="function call followed by function response",
        operations=(
            _text("user", "search for replay consistency"),
            ReplayOperation(
                "event", {
                    "event_type": "function_call",
                    "author": "assistant",
                    "name": "search",
                    "call_id": "search-call-1",
                    "args": {
                        "query": "replay consistency"
                    },
                }),
            ReplayOperation(
                "event", {
                    "event_type": "function_response",
                    "author": "tool",
                    "name": "search",
                    "call_id": "search-call-1",
                    "response": {
                        "result": "consistent"
                    },
                }),
            _text("assistant", "the search result is consistent"),
        ),
    ),
    ReplayCase(
        case_id="state_overwrite",
        description="repeated session state writes and overwrite",
        operations=(
            _state({
                "phase": "draft",
                "counter": 1
            }),
            _state({
                "phase": "review",
                "counter": 2
            }),
            _state({
                "phase": "done",
                "counter": 3
            }),
        ),
    ),
    ReplayCase(
        case_id="scoped_state",
        description="application, user, session, and temporary state",
        operations=(
            _state({
                "app:release": "2026",
                "user:language": "python",
                "session_flag": "active"
            }),
            _state({
                "user:language": "go",
                "temp:request_id": "ephemeral",
                "session_flag": "complete"
            }),
        ),
    ),
    ReplayCase(
        case_id="memory_roundtrip",
        description="store and retrieve one user preference memory",
        operations=(
            _text("user", "preference-token-oolong means I prefer oolong tea"),
            _text("assistant", "I will remember that preference"),
            ReplayOperation("store_memory"),
        ),
        memory_queries=("preference-token-oolong", ),
    ),
    ReplayCase(
        case_id="summary_create",
        description="create a deterministic summary for a long dialogue",
        operations=(*_dialogue("summary-create", 4),
                    ReplayOperation("summarize", {"summary_text": "summary version one"})),
    ),
    ReplayCase(
        case_id="summary_update",
        description="preserve an old summary on failure, then replace it",
        operations=(*_dialogue("summary-update-initial", 4),
                    ReplayOperation("summarize", {"summary_text": "summary update version one"}),
                    *_dialogue("summary-update-later", 3), ReplayOperation("summarize_failure"),
                    ReplayOperation("summarize", {"summary_text": "summary update version two"})),
    ),
    ReplayCase(
        case_id="summary_truncation",
        description="summary, retained events, and new events restore context together",
        operations=(*_dialogue("truncation-old", 5),
                    ReplayOperation("summarize", {"summary_text": "compressed historical context"}),
                    _text("user", "follow-up after compression"), _text("assistant", "answer after compression")),
    ),
    ReplayCase(
        case_id="partial_retry",
        description="interrupted write leaves no dirty state and retry is stored once",
        operations=(
            _text("user", "produce a streamed answer"),
            _text(
                "assistant",
                "unfinished",
                event_id="retry-event",
                partial=True,
                state_delta={
                    "recovery_status": "dirty",
                    "temp:retry_buffer": "unfinished",
                },
            ),
            _text(
                "assistant",
                "finished answer",
                event_id="retry-event",
                state_delta={"recovery_status": "complete"},
            ),
        ),
    ),
)
