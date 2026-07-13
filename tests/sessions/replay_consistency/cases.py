# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Deterministic replay cases for session/memory/summary consistency tests."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass(frozen=True)
class ReplayOp:
    """One logical operation in a replay case."""

    kind: str
    session_id: str = "s1"
    user_id: str = "user1"
    app_name: str = "replay_app"
    client_event_id: str | None = None
    client_summary_id: str | None = None
    author: str = "user"
    text: str = ""
    state_delta: dict[str, Any] = field(default_factory=dict)
    initial_state: dict[str, Any] = field(default_factory=dict)
    function_call_id: str | None = None
    function_name: str = "lookup"
    function_args: dict[str, Any] = field(default_factory=dict)
    function_response: dict[str, Any] = field(default_factory=dict)
    query: str = ""
    probe_id: str | None = None


@dataclass(frozen=True)
class ReplayCase:
    """A deterministic replay case that can be executed against any adapter."""

    case_id: str
    operations: tuple[ReplayOp, ...]
    checkpoints: tuple[str, ...] = ("final",)
    expected_entities: tuple[str, ...] = ("events", "state", "memory", "summary")
    description: str = ""


def _create(session_id: str = "s1", user_id: str = "user1", state: dict[str, Any] | None = None) -> ReplayOp:
    return ReplayOp(kind="create_session", session_id=session_id, user_id=user_id, initial_state=state or {})


def _text(
    client_event_id: str,
    text: str,
    *,
    session_id: str = "s1",
    user_id: str = "user1",
    author: str = "user",
    state_delta: dict[str, Any] | None = None,
) -> ReplayOp:
    return ReplayOp(
        kind="append_text",
        session_id=session_id,
        user_id=user_id,
        client_event_id=client_event_id,
        author=author,
        text=text,
        state_delta=state_delta or {},
    )


def _store(session_id: str = "s1", user_id: str = "user1") -> ReplayOp:
    return ReplayOp(kind="store_memory", session_id=session_id, user_id=user_id)


def _probe(probe_id: str, query: str, *, session_id: str = "s1", user_id: str = "user1") -> ReplayOp:
    return ReplayOp(kind="search_memory", session_id=session_id, user_id=user_id, probe_id=probe_id, query=query)


def _summary(client_summary_id: str, *, session_id: str = "s1", user_id: str = "user1") -> ReplayOp:
    return ReplayOp(kind="summarize", session_id=session_id, user_id=user_id, client_summary_id=client_summary_id)


def standard_cases() -> list[ReplayCase]:
    """Return the public deterministic replay cases.

    The cases intentionally use unique searchable words so memory result order
    does not become an accidental backend-specific assertion.
    """

    return [
        ReplayCase(
            case_id="single_turn_text",
            description="One user message and one assistant response.",
            operations=(
                _create(),
                _text("u1", "alpha preference is tea", author="user"),
                _text("a1", "noted alpha preference", author="assistant"),
                _store(),
                _probe("alpha", "alpha"),
            ),
        ),
        ReplayCase(
            case_id="multi_turn_text",
            description="Three deterministic user/assistant turns.",
            operations=(
                _create(),
                _text("u1", "bravo question one", author="user"),
                _text("a1", "bravo answer one", author="assistant"),
                _text("u2", "charlie question two", author="user"),
                _text("a2", "charlie answer two", author="assistant"),
                _text("u3", "delta question three", author="user"),
                _text("a3", "delta answer three", author="assistant"),
                _store(),
                _probe("delta", "delta"),
            ),
        ),
        ReplayCase(
            case_id="tool_call_response",
            description="Function call, matching response, and an error-shaped response payload.",
            operations=(
                _create(),
                ReplayOp(
                    kind="append_tool_call",
                    client_event_id="tool_call",
                    author="assistant",
                    function_call_id="call-weather-1",
                    function_name="weather",
                    function_args={"city": "Shenzhen", "unit": "celsius"},
                ),
                ReplayOp(
                    kind="append_tool_response",
                    client_event_id="tool_response",
                    author="user",
                    function_call_id="call-weather-1",
                    function_name="weather",
                    function_response={"temperature": 28, "condition": "sunny"},
                ),
                ReplayOp(
                    kind="append_tool_response",
                    client_event_id="tool_error",
                    author="user",
                    function_call_id="call-weather-1",
                    function_name="weather",
                    function_response={"error": "rate_limited"},
                ),
            ),
        ),
        ReplayCase(
            case_id="state_shallow_update",
            description="Session, app, and user state updates with shallow replacement semantics.",
            operations=(
                _create(state={"profile": {"name": "Ada", "city": "SZ"}, "counter": 1}),
                _text("state1", "echo state start", state_delta={"counter": 2}),
                _text("state2", "echo nested replace", state_delta={"profile": {"city": "BJ"}}),
                _text("state3", "echo nullable", state_delta={"nullable": None}),
            ),
        ),
        ReplayCase(
            case_id="memory_scope_user_session",
            description="Same user across sessions and different user isolation.",
            operations=(
                _create("s1", "user1"),
                _create("s2", "user1"),
                _create("s3", "user2"),
                _text("s1u", "foxtrot user one session one", session_id="s1", user_id="user1"),
                _text("s2u", "golf user one session two", session_id="s2", user_id="user1"),
                _text("s3u", "hotel user two private", session_id="s3", user_id="user2"),
                _store("s1", "user1"),
                _store("s2", "user1"),
                _store("s3", "user2"),
                _probe("same_user_s1", "foxtrot", session_id="s1", user_id="user1"),
                _probe("same_user_s2", "golf", session_id="s1", user_id="user1"),
                _probe("other_user", "hotel", session_id="s3", user_id="user2"),
                _probe("no_cross_user", "hotel", session_id="s1", user_id="user1"),
            ),
        ),
        ReplayCase(
            case_id="summary_create_update",
            description="Create and then update a session summary.",
            operations=(
                _create(),
                _text("u1", "india summary turn one", author="user"),
                _text("a1", "india assistant one", author="assistant"),
                _text("u2", "juliet summary turn two", author="user"),
                _text("a2", "juliet assistant two", author="assistant"),
                _summary("sum1"),
                _text("u3", "kilo after first summary", author="user"),
                _text("a3", "kilo assistant after summary", author="assistant"),
                _summary("sum2"),
            ),
        ),
        ReplayCase(
            case_id="summary_event_truncation",
            description="Summary plus retained recent events, followed by new events.",
            operations=(
                _create(),
                _text("u1", "lima old one", author="user"),
                _text("a1", "lima old answer", author="assistant"),
                _text("u2", "mike old two", author="user"),
                _text("a2", "mike old answer", author="assistant"),
                _text("u3", "november recent", author="user"),
                _summary("sum1"),
                _text("a3", "november new answer", author="assistant"),
                _store(),
                _probe("november", "november"),
            ),
        ),
        ReplayCase(
            case_id="failure_retry_append",
            description="Same logical event retried after an ambiguous acknowledgement.",
            operations=(
                _create(),
                _text("retry1", "oscar retry logical event", author="user"),
                _text("retry1_retry", "oscar retry logical event", author="user"),
                _store(),
                _probe("oscar", "oscar"),
            ),
        ),
        ReplayCase(
            case_id="cross_session_isolation",
            description="Two sessions for one user with separate state and summaries.",
            operations=(
                _create("s1", "user1", {"topic": "papa"}),
                _create("s2", "user1", {"topic": "quebec"}),
                _text("s1u1", "papa session one user", session_id="s1", user_id="user1"),
                _text("s1a1", "papa session one assistant", session_id="s1", user_id="user1", author="assistant"),
                _text("s1u2", "papa session one second", session_id="s1", user_id="user1"),
                _summary("s1sum", session_id="s1", user_id="user1"),
                _text("s2u1", "quebec session two user", session_id="s2", user_id="user1"),
                _text("s2a1", "quebec session two assistant", session_id="s2", user_id="user1", author="assistant"),
                _text("s2u2", "quebec session two second", session_id="s2", user_id="user1"),
                _summary("s2sum", session_id="s2", user_id="user1"),
            ),
        ),
        ReplayCase(
            case_id="summary_defect_specials",
            description="Dense summary scenario used by summary mutation tests.",
            operations=(
                _create(),
                _text("u1", "romeo summary one", author="user"),
                _text("a1", "romeo answer one", author="assistant"),
                _text("u2", "sierra summary two", author="user"),
                _text("a2", "sierra answer two", author="assistant"),
                _summary("sum1"),
                _text("u3", "tango summary three", author="user"),
                _text("a3", "tango answer three", author="assistant"),
                _summary("sum2"),
            ),
        ),
        ReplayCase(
            case_id="all_entities_contract",
            description="Compact case with text, tool, state, memory, and summary entities.",
            operations=(
                _create(state={"contract": "start"}),
                _text("u1", "uniform all entity user", state_delta={"contract": "updated"}),
                ReplayOp(
                    kind="append_tool_call",
                    client_event_id="tc1",
                    author="assistant",
                    function_call_id="call-contract-1",
                    function_name="contract_tool",
                    function_args={"value": "uniform"},
                ),
                ReplayOp(
                    kind="append_tool_response",
                    client_event_id="tr1",
                    author="user",
                    function_call_id="call-contract-1",
                    function_name="contract_tool",
                    function_response={"ok": True},
                ),
                _text("a1", "uniform all entity assistant", author="assistant"),
                _summary("sum1"),
                _store(),
                _probe("uniform", "uniform"),
            ),
        ),
    ]
