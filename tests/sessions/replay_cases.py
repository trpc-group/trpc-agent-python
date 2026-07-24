# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standard replay cases for Session / Memory / Summary consistency testing.

Each ``ReplayCase`` is a deterministic trace that can be executed against
any backend implementation.  Fault variants share the same base trace but
are marked with ``expected_faults`` and paired with a ``FaultSpec`` in the
test driver.
"""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import State

from .replay_consistency_framework import AppendEventOp
from .replay_consistency_framework import CreateSessionOp
from .replay_consistency_framework import CreateSummaryOp
from .replay_consistency_framework import FaultSpec
from .replay_consistency_framework import ReplayCase
from .replay_consistency_framework import SearchMemoryOp
from .replay_consistency_framework import StoreMemoryOp
from .replay_consistency_framework import UpdateStateOp


def _event(author: str, text: str, invocation_id: str = "inv-1") -> Event:
    return Event(
        invocation_id=invocation_id,
        author=author,
        content=Content(parts=[Part.from_text(text=text)]),
    )


def _event_with_function_call(name: str, args: dict) -> Event:
    return Event(
        invocation_id="inv-tool",
        author="agent",
        content=Content(parts=[Part(function_call=FunctionCall(name=name, args=args))]),
    )


def _event_with_function_response(name: str, response: dict) -> Event:
    return Event(
        invocation_id="inv-tool",
        author="tool",
        content=Content(parts=[Part(function_response=FunctionResponse(name=name, response=response))]),
    )


def _state_delta_event(delta: dict[str, Any]) -> Event:
    return Event(
        invocation_id="inv-state",
        author="system",
        actions=EventActions(state_delta=delta),
        content=Content(parts=[Part.from_text(text="state update")]),
    )


# ---------------------------------------------------------------------------
# Normal cases
# ---------------------------------------------------------------------------


def _single_turn() -> ReplayCase:
    return ReplayCase(
        name="single_turn_simple",
        description="Single user input and agent text output.",
        operations=[
            CreateSessionOp("app", "user", "s1"),
            AppendEventOp(0, _event("user", "Hello")),
            AppendEventOp(0, _event("agent", "Hi there")),
        ],
    )


def _multi_turn() -> ReplayCase:
    return ReplayCase(
        name="multi_turn_conversation",
        description="Continuous multi-turn user/assistant conversation.",
        operations=[
            CreateSessionOp("app", "user", "s2"),
            AppendEventOp(0, _event("user", "What is Python?")),
            AppendEventOp(0, _event("agent", "A programming language.")),
            AppendEventOp(0, _event("user", "Show me an example.")),
            AppendEventOp(0, _event("agent", "print('hello')")),
        ],
    )


def _tool_call() -> ReplayCase:
    return ReplayCase(
        name="tool_call_conversation",
        description="Conversation containing function_call and function_response.",
        operations=[
            CreateSessionOp("app", "user", "s3"),
            AppendEventOp(0, _event("user", "What is the weather?")),
            AppendEventOp(0, _event_with_function_call("get_weather", {"city": "Beijing"})),
            AppendEventOp(0, _event_with_function_response("get_weather", {"temperature": 25})),
            AppendEventOp(0, _event("agent", "It is 25 degrees in Beijing.")),
        ],
    )


def _state_updates() -> ReplayCase:
    return ReplayCase(
        name="state_updates",
        description="Multiple session/app/user scoped state writes.",
        operations=[
            CreateSessionOp("app", "user", "s4", state={
                "topic": "travel",
                f"{State.APP_PREFIX}app_mode": "debug",
                f"{State.USER_PREFIX}user_lang": "zh",
            }),
            AppendEventOp(0, _event("user", "Plan a trip")),
            AppendEventOp(0, _state_delta_event({"destination": "Paris"})),
            AppendEventOp(0, _state_delta_event({"destination": "Tokyo"})),
            AppendEventOp(0, _state_delta_event({f"{State.USER_PREFIX}user_lang": "en"})),
        ],
    )


def _memory_fact() -> ReplayCase:
    return ReplayCase(
        name="memory_write_read",
        description="Store user preference facts and search memory.",
        operations=[
            CreateSessionOp("app", "user", "s5"),
            AppendEventOp(0, _event("user", "I prefer dark mode.")),
            AppendEventOp(0, _event("agent", "Noted, I will remember dark mode.")),
            StoreMemoryOp(0),
            SearchMemoryOp(0, "dark mode"),
        ],
    )


def _summary_generation() -> ReplayCase:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return ReplayCase(
        name="summary_generation",
        description="Long conversation triggers summary generation.",
        operations=[
            CreateSessionOp("app", "user", "s6"),
            AppendEventOp(0, _event("user", "Message 1", "inv-1")),
            AppendEventOp(0, _event("agent", "Reply 1", "inv-1")),
            AppendEventOp(0, _event("user", "Message 2", "inv-2")),
            AppendEventOp(0, _event("agent", "Reply 2", "inv-2")),
            AppendEventOp(0, _event("user", "Message 3", "inv-3")),
            AppendEventOp(0, _event("agent", "Reply 3", "inv-3")),
            CreateSummaryOp(0, force=True),
        ],
        config=config,
    )


def _summary_truncation() -> ReplayCase:
    config = SessionServiceConfig(store_historical_events=True)
    config.clean_ttl_config()
    return ReplayCase(
        name="summary_event_truncation",
        description="Summary compression keeps recent events and summary anchor.",
        operations=[
            CreateSessionOp("app", "user", "s7"),
            AppendEventOp(0, _event("user", "Old context A", "inv-1")),
            AppendEventOp(0, _event("agent", "Old reply A", "inv-1")),
            AppendEventOp(0, _event("user", "Old context B", "inv-2")),
            AppendEventOp(0, _event("agent", "Old reply B", "inv-2")),
            AppendEventOp(0, _event("user", "Recent question", "inv-3")),
            AppendEventOp(0, _event("agent", "Recent answer", "inv-3")),
            CreateSummaryOp(0, force=True),
        ],
        config=config,
    )


def _anomaly_recovery() -> ReplayCase:
    return ReplayCase(
        name="anomaly_recovery_duplicate",
        description="Backend receives a duplicate append but remains consistent.",
        operations=[
            CreateSessionOp("app", "user", "s8"),
            AppendEventOp(0, _event("user", "Hello")),
            AppendEventOp(0, _event("agent", "Hi")),
        ],
    )


# ---------------------------------------------------------------------------
# Fault-injected cases
# ---------------------------------------------------------------------------


def _injected_drop_event() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_drop_event",
        description="Target backend drops the first event.",
        operations=[
            CreateSessionOp("app", "user", "s-drop"),
            AppendEventOp(0, _event("user", "First")),
            AppendEventOp(0, _event("agent", "Second")),
            AppendEventOp(0, _event("user", "Third")),
        ],
        expected_faults=["drop_event"],
    )
    return case, FaultSpec("drop_event", {"session_index": 0, "event_index": 0})


def _injected_duplicate_event() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_duplicate_event",
        description="Target backend duplicates the second event.",
        operations=[
            CreateSessionOp("app", "user", "s-dup"),
            AppendEventOp(0, _event("user", "First")),
            AppendEventOp(0, _event("agent", "Second")),
        ],
        expected_faults=["duplicate_event"],
    )
    return case, FaultSpec("duplicate_event", {"session_index": 0, "event_index": 1})


def _injected_corrupt_state() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_corrupt_state",
        description="Target backend corrupts a session state key.",
        operations=[
            CreateSessionOp("app", "user", "s-state"),
            AppendEventOp(0, _event("user", "Set value")),
            AppendEventOp(0, _state_delta_event({"counter": 42})),
        ],
        expected_faults=["corrupt_state"],
    )
    return case, FaultSpec("corrupt_state", {"session_index": 0, "state_patch": {"counter": 9999}})


def _injected_drop_summary() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_drop_summary",
        description="Target backend loses the generated summary.",
        operations=[
            CreateSessionOp("app", "user", "s-sum-drop"),
            AppendEventOp(0, _event("user", "A", "inv-1")),
            AppendEventOp(0, _event("agent", "B", "inv-1")),
            AppendEventOp(0, _event("user", "C", "inv-2")),
            AppendEventOp(0, _event("agent", "D", "inv-2")),
            CreateSummaryOp(0, force=True),
        ],
        expected_faults=["drop_summary"],
    )
    return case, FaultSpec("drop_summary", {"session_index": 0})


def _injected_wrong_summary_session() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_wrong_summary_session",
        description="Target backend stores summary under the wrong session id.",
        operations=[
            CreateSessionOp("app", "user", "s-sum-wrong"),
            AppendEventOp(0, _event("user", "A", "inv-1")),
            AppendEventOp(0, _event("agent", "B", "inv-1")),
            AppendEventOp(0, _event("user", "C", "inv-2")),
            AppendEventOp(0, _event("agent", "D", "inv-2")),
            CreateSummaryOp(0, force=True),
        ],
        expected_faults=["wrong_summary_session"],
    )
    return case, FaultSpec("wrong_summary_session", {"session_index": 0, "wrong_session_id": "other-session"})


def _injected_summary_loss() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_summary_loss",
        description="Target backend keeps summary metadata but empties summary text.",
        operations=[
            CreateSessionOp("app", "user", "s-sum-loss"),
            AppendEventOp(0, _event("user", "A", "inv-1")),
            AppendEventOp(0, _event("agent", "B", "inv-1")),
            AppendEventOp(0, _event("user", "C", "inv-2")),
            AppendEventOp(0, _event("agent", "D", "inv-2")),
            CreateSummaryOp(0, force=True),
        ],
        expected_faults=["summary_loss"],
    )
    return case, FaultSpec("summary_loss", {"session_index": 0})


def _injected_summary_override_error() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_summary_override_error",
        description="Target backend overrides summary text with incorrect content.",
        operations=[
            CreateSessionOp("app", "user", "s-sum-override"),
            AppendEventOp(0, _event("user", "A", "inv-1")),
            AppendEventOp(0, _event("agent", "B", "inv-1")),
            AppendEventOp(0, _event("user", "C", "inv-2")),
            AppendEventOp(0, _event("agent", "D", "inv-2")),
            CreateSummaryOp(0, force=True),
        ],
        expected_faults=["summary_override_error"],
    )
    return case, FaultSpec("summary_override_error", {"session_index": 0, "wrong_summary_text": "corrupted summary"})


def _injected_drop_memory_event() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_drop_memory_event",
        description="Target memory backend drops the stored event.",
        operations=[
            CreateSessionOp("app", "user", "s-mem-drop"),
            AppendEventOp(0, _event("user", "remember this keyword")),
            AppendEventOp(0, _event("agent", "I stored it")),
            StoreMemoryOp(0),
            SearchMemoryOp(0, "keyword"),
        ],
        expected_faults=["drop_memory_event"],
    )
    return case, FaultSpec("drop_event", {"session_index": 0, "event_index": 0})


def _injected_extra_event() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_extra_event",
        description="Target backend adds an extra event that did not happen.",
        operations=[
            CreateSessionOp("app", "user", "s-extra"),
            AppendEventOp(0, _event("user", "Hello")),
            AppendEventOp(0, _event("agent", "Hi")),
        ],
        expected_faults=["extra_event"],
    )
    return case, FaultSpec("duplicate_event", {"session_index": 0, "event_index": 1})


def _injected_app_state_loss() -> tuple[ReplayCase, FaultSpec]:
    case = ReplayCase(
        name="injected_app_state_loss",
        description="Target backend loses an app-scoped state key.",
        operations=[
            CreateSessionOp("app", "user", "s-app-state", state={
                f"{State.APP_PREFIX}feature_flag": "on",
            }),
            AppendEventOp(0, _event("user", "Hello")),
        ],
        expected_faults=["corrupt_state"],
    )
    return case, FaultSpec("corrupt_state", {"session_index": 0, "state_patch": {f"{State.APP_PREFIX}feature_flag": "off"}})


# ---------------------------------------------------------------------------
# Public collections
# ---------------------------------------------------------------------------


def get_normal_cases() -> list[ReplayCase]:
    return [
        _single_turn(),
        _multi_turn(),
        _tool_call(),
        _state_updates(),
        _memory_fact(),
        _summary_generation(),
        _summary_truncation(),
        _anomaly_recovery(),
    ]


def get_fault_cases() -> list[tuple[ReplayCase, FaultSpec]]:
    return [
        _injected_drop_event(),
        _injected_duplicate_event(),
        _injected_corrupt_state(),
        _injected_drop_summary(),
        _injected_wrong_summary_session(),
        _injected_summary_loss(),
        _injected_summary_override_error(),
        _injected_drop_memory_event(),
        _injected_extra_event(),
        _injected_app_state_loss(),
    ]


def get_all_cases_with_faults() -> tuple[list[ReplayCase], dict[str, FaultSpec]]:
    """Return all cases and a mapping from fault case name to FaultSpec."""
    normal = get_normal_cases()
    fault_tuples = get_fault_cases()
    cases = normal + [c for c, _ in fault_tuples]
    faults = {c.name: f for c, f in fault_tuples}
    return cases, faults
