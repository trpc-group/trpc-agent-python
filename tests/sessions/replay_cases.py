# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standard replay traces for session, memory, and summary backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OpKind(str, Enum):
    """Operations understood by the replay harness."""

    CREATE_SESSION = "create_session"
    APPEND_EVENT = "append_event"
    GET_SESSION = "get_session"
    LIST_SESSIONS = "list_sessions"
    DELETE_SESSION = "delete_session"
    STORE_MEMORY = "store_memory"
    SEARCH_MEMORY = "search_memory"
    CREATE_SUMMARY = "create_summary"
    GET_SUMMARY = "get_summary"
    RESET_SUMMARY_READER = "reset_summary_reader"
    INJECT_APPEND_FAILURE = "inject_append_failure"


@dataclass(frozen=True)
class ReplayCase:
    """A backend-independent operation trace and its observable contracts."""

    name: str
    operations: list[dict[str, Any]]
    known_diffs: list[dict[str, str]] = field(default_factory=list)


_BASE_TIMESTAMP = 1_700_000_000.0


def _event(
    case: str,
    index: int,
    author: str,
    *,
    text: str | None = None,
    role: str | None = None,
    part: dict[str, Any] | None = None,
    state_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content_part = part if part is not None else {"text": text or ""}
    return {
        "id": f"{case}-event-{index:02d}",
        "invocation_id": f"{case}-invocation-{index:02d}",
        "author": author,
        "content": {
            "role": role or ("user" if author == "user" else "model"),
            "parts": [content_part],
        },
        "actions": {
            "state_delta": state_delta or {}
        },
        "timestamp": _BASE_TIMESTAMP + index,
    }


def _dialogue(case: str, turns: int, start_index: int = 1) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    event_index = start_index
    for turn in range(1, turns + 1):
        operations.append({
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(case, event_index, "user", text=f"Question {turn}"),
        })
        event_index += 1
        operations.append({
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(case, event_index, "assistant", text=f"Answer {turn}"),
        })
        event_index += 1
    return operations


def _create(case: str, session_id: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "op": OpKind.CREATE_SESSION.value,
        "app_name": "replay_app",
        "user_id": "replay_user",
        "session_id": session_id,
        "state": state or {},
    }


def build_single_turn_text() -> ReplayCase:
    name = "single_turn_text"
    return ReplayCase(name, [
        _create(name, "replay-single"),
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 1, "user", text="Hello")
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 2, "assistant", text="Hello!")
        },
        {
            "op": OpKind.GET_SESSION.value,
            "expect": {
                "session_exists": True,
                "event_count": 2,
                "event_texts": ["Hello", "Hello!"]
            },
        },
    ])


def build_multi_turn_text() -> ReplayCase:
    name = "multi_turn_text"
    operations = [_create(name, "replay-multi"), *_dialogue(name, 3)]
    operations.append({
        "op": OpKind.GET_SESSION.value,
        "expect": {
            "event_count": 6,
            "event_texts": [
                "Question 1",
                "Answer 1",
                "Question 2",
                "Answer 2",
                "Question 3",
                "Answer 3",
            ]
        },
    })
    return ReplayCase(name, operations)


def build_tool_call_response() -> ReplayCase:
    name = "tool_call_response"
    return ReplayCase(name, [
        _create(name, "replay-tool"),
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 1, "user", text="Weather in Shenzhen?")
        },
        {
            "op":
            OpKind.APPEND_EVENT.value,
            "event":
            _event(name,
                   2,
                   "assistant",
                   part={
                       "function_call": {
                           "id": "weather-call",
                           "name": "get_weather",
                           "args": {
                               "city": "Shenzhen"
                           }
                       },
                   }),
        },
        {
            "op":
            OpKind.APPEND_EVENT.value,
            "event":
            _event(name,
                   3,
                   "tool",
                   part={
                       "function_response": {
                           "id": "weather-call",
                           "name": "get_weather",
                           "response": {
                               "temperature": 30,
                               "condition": "sunny"
                           },
                       },
                   }),
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 4, "assistant", text="It is sunny and 30C.")
        },
        {
            "op": OpKind.GET_SESSION.value,
            "expect": {
                "event_count": 4,
                "function_call_count": 1,
                "function_response_count": 1
            }
        },
    ])


def build_state_basic_update() -> ReplayCase:
    name = "state_basic_update"
    return ReplayCase(name, [
        _create(name, "replay-state", {"counter": 0}),
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 1, "user", text="draft", state_delta={
                "counter": 1,
                "mode": "draft"
            }),
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 2, "assistant", text="done", state_delta={
                "counter": 2,
                "mode": "final"
            }),
        },
        {
            "op": OpKind.GET_SESSION.value,
            "expect": {
                "event_count": 2,
                "state": {
                    "counter": 2,
                    "mode": "final"
                }
            }
        },
    ])


def build_state_three_tier() -> ReplayCase:
    name = "state_three_tier"
    initial = {"app:theme": "light", "user:language": "en", "step": 0}
    delta = {"app:theme": "dark", "user:language": "zh", "step": 1, "temp:request_token": "ephemeral"}
    return ReplayCase(name, [
        _create(name, "replay-state-tier", initial),
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 1, "user", text="update", state_delta=delta)
        },
        {
            "op": OpKind.GET_SESSION.value,
            "expect": {
                "state": {
                    "app:theme": "dark",
                    "user:language": "zh",
                    "step": 1,
                },
                "event_count": 1
            }
        },
    ])


def build_memory_store_search() -> ReplayCase:
    name = "memory_store_search"
    return ReplayCase(name, [
        _create(name, "replay-memory"),
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 1, "user", text="I prefer dark roast coffee")
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 2, "assistant", text="I will remember your coffee preference")
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 3, "user", text="Python is my favorite language")
        },
        {
            "op": OpKind.STORE_MEMORY.value
        },
        {
            "op": OpKind.SEARCH_MEMORY.value,
            "query": "coffee",
            "expect": {
                "memory_count": 2
            }
        },
        {
            "op": OpKind.SEARCH_MEMORY.value,
            "query": "Python",
            "expect": {
                "memory_count": 1
            }
        },
    ])


def build_memory_multi_session() -> ReplayCase:
    name = "memory_multi_session"
    return ReplayCase(name, [
        _create(name, "replay-memory-a"),
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 1, "user", text="I am vegetarian")
        },
        {
            "op": OpKind.STORE_MEMORY.value
        },
        _create(name, "replay-memory-b"),
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 2, "user", text="Find a vegetarian restaurant")
        },
        {
            "op": OpKind.STORE_MEMORY.value
        },
        {
            "op": OpKind.SEARCH_MEMORY.value,
            "query": "vegetarian",
            "expect": {
                "memory_count": 2
            }
        },
    ])


def build_summary_generation() -> ReplayCase:
    name = "summary_generation"
    operations = [_create(name, "replay-summary"), *_dialogue(name, 4)]
    operations.extend([
        {
            "op": OpKind.CREATE_SUMMARY.value,
            "keep_recent_count": 2
        },
        {
            "op": OpKind.GET_SUMMARY.value,
            "expect": {
                "summary": {
                    "session_id":
                    "replay-summary",
                    "summary_text": ("Mock summary: preserve user preferences, decisions, tool results, and "
                                     "unresolved follow-up questions."),
                    "original_event_count":
                    8,
                    "compressed_event_count":
                    3,
                    "version":
                    1,
                    "updated_at_present":
                    True,
                },
                "summary_event_count": 1,
                "event_count": 3,
            }
        },
        {
            "op": OpKind.RESET_SUMMARY_READER.value
        },
        {
            "op": OpKind.GET_SUMMARY.value,
            "expect": {
                "summary": {
                    "session_id":
                    "replay-summary",
                    "summary_text": ("Mock summary: preserve user preferences, decisions, tool results, and "
                                     "unresolved follow-up questions."),
                    "version":
                    1,
                    "updated_at_present":
                    True,
                },
                "summary_event_count": 1,
            }
        },
    ])
    return ReplayCase(name, operations)


def build_summary_truncation() -> ReplayCase:
    name = "summary_truncation"
    operations = [_create(name, "replay-summary-truncate"), *_dialogue(name, 5)]
    operations.extend([
        {
            "op": OpKind.CREATE_SUMMARY.value,
            "keep_recent_count": 4
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 11, "user", text="Question after summary")
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": _event(name, 12, "assistant", text="Answer after summary")
        },
        {
            "op": OpKind.GET_SESSION.value,
            "expect": {
                "event_count": 7,
                "historical_event_count": 6,
                "summary_event_count": 1,
                "event_text_suffix": ["Question after summary", "Answer after summary"],
            }
        },
        {
            "op": OpKind.GET_SUMMARY.value,
            "expect": {
                "summary": {
                    "session_id": "replay-summary-truncate",
                    "original_event_count": 10,
                    "compressed_event_count": 5,
                    "version": 1,
                    "updated_at_present": True,
                },
            }
        },
    ])
    return ReplayCase(name, operations)


def build_summary_error_detection() -> ReplayCase:
    """Exercise summary overwrite/versioning and session ownership."""
    name = "summary_update_and_ownership"
    operations = [_create(name, "replay-summary-owner-a"), *_dialogue(name, 4)]
    operations.extend([
        {
            "op": OpKind.CREATE_SUMMARY.value,
            "keep_recent_count": 2
        },
        *_dialogue(name, 3, start_index=9),
        {
            "op": OpKind.CREATE_SUMMARY.value,
            "keep_recent_count": 2
        },
        {
            "op": OpKind.GET_SUMMARY.value,
            "expect": {
                "summary": {
                    "session_id": "replay-summary-owner-a",
                    "original_event_count": 9,
                    "compressed_event_count": 3,
                    "version": 2,
                    "updated_at_present": True,
                },
                "summary_event_count": 1,
            }
        },
        _create(name, "replay-summary-owner-b"),
        *_dialogue(name, 3, start_index=20),
        {
            "op": OpKind.CREATE_SUMMARY.value,
            "keep_recent_count": 2
        },
        {
            "op": OpKind.GET_SUMMARY.value,
            "expect": {
                "summary": {
                    "session_id": "replay-summary-owner-b",
                    "original_event_count": 6,
                    "compressed_event_count": 3,
                    "version": 1,
                    "updated_at_present": True,
                },
            }
        },
        {
            "op": OpKind.GET_SUMMARY.value,
            "session_ref": "replay-summary-owner-a",
            "expect": {
                "summary": {
                    "session_id": "replay-summary-owner-a",
                    "version": 2,
                    "updated_at_present": True,
                },
            }
        },
    ])
    return ReplayCase(name, operations)


def build_error_duplicate_write() -> ReplayCase:
    name = "error_recovery"
    failed_event = _event(name, 1, "user", text="retry me", state_delta={"status": "dirty"})
    return ReplayCase(name, [
        _create(name, "replay-recovery", {"status": "clean"}),
        {
            "op": OpKind.INJECT_APPEND_FAILURE.value,
            "event": failed_event
        },
        {
            "op": OpKind.APPEND_EVENT.value,
            "event": failed_event
        },
        {
            "op": OpKind.STORE_MEMORY.value
        },
        {
            "op": OpKind.STORE_MEMORY.value
        },
        {
            "op": OpKind.SEARCH_MEMORY.value,
            "query": "retry",
            "expect": {
                "memory_count": 1
            }
        },
        {
            "op": OpKind.GET_SESSION.value,
            "expect": {
                "event_count": 1,
                "state": {
                    "status": "dirty"
                }
            }
        },
    ])


def all_replay_cases() -> list[ReplayCase]:
    """Return every public replay case in deterministic order."""
    return [
        build_single_turn_text(),
        build_multi_turn_text(),
        build_tool_call_response(),
        build_state_basic_update(),
        build_state_three_tier(),
        build_memory_store_search(),
        build_memory_multi_session(),
        build_summary_generation(),
        build_summary_truncation(),
        build_summary_error_detection(),
        build_error_duplicate_write(),
    ]
