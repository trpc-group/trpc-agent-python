"""Deterministic replay cases for session/memory/summary backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    invocation_id: str
    author: str
    role: str
    text: str | None
    function_call: dict | None
    function_response: dict | None
    state_delta: dict[str, Any] | None
    branch: str | None
    tag: str | None
    filter_key: str | None
    partial: bool = False
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class MemoryQuerySpec:
    key: str | None
    query: str
    limit: int
    expected_text_fragments: list[str]


@dataclass(frozen=True)
class ReplayCase:
    name: str
    app_name: str
    user_id: str
    session_id: str
    initial_state: dict[str, Any]
    events: list[EventSpec]
    memory_queries: list[MemoryQuerySpec]
    summary_points: list[int]
    description: str


def _text_event(
    case_name: str,
    index: int,
    *,
    invocation_id: str,
    author: str,
    role: str,
    text: str,
    state_delta: dict[str, Any] | None = None,
    branch: str | None = None,
    tag: str | None = None,
    filter_key: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> EventSpec:
    return EventSpec(
        event_id=f"{case_name}-event-{index:02d}",
        invocation_id=invocation_id,
        author=author,
        role=role,
        text=text,
        function_call=None,
        function_response=None,
        state_delta=state_delta,
        branch=branch,
        tag=tag,
        filter_key=filter_key,
        error_code=error_code,
        error_message=error_message,
    )


def replay_cases() -> list[ReplayCase]:
    """Return the public replay corpus. Keep names/order stable."""
    return [
        ReplayCase(
            name="single_turn_text",
            app_name="replay-app",
            user_id="user-single",
            session_id="session-001",
            initial_state={},
            events=[
                _text_event(
                    "single_turn_text",
                    0,
                    invocation_id="inv-single-1",
                    author="user",
                    role="user",
                    text="Hello replay bot.",
                ),
                _text_event(
                    "single_turn_text",
                    1,
                    invocation_id="inv-single-1",
                    author="assistant",
                    role="model",
                    text="Hello! Replay is deterministic.",
                ),
            ],
            memory_queries=[
                MemoryQuerySpec(
                    key=None,
                    query="Hello",
                    limit=10,
                    expected_text_fragments=["Hello replay bot.", "Replay is deterministic."],
                )
            ],
            summary_points=[],
            description="Two text events preserve order, author, role, and text.",
        ),
        ReplayCase(
            name="multi_turn_append_order",
            app_name="replay-app",
            user_id="user-order",
            session_id="session-002",
            initial_state={},
            events=[
                _text_event(
                    "multi_turn_append_order",
                    0,
                    invocation_id="inv-order-1",
                    author="user",
                    role="user",
                    text="First question about replay order.",
                ),
                _text_event(
                    "multi_turn_append_order",
                    1,
                    invocation_id="inv-order-1",
                    author="assistant",
                    role="model",
                    text="First answer keeps the same invocation.",
                ),
                _text_event(
                    "multi_turn_append_order",
                    2,
                    invocation_id="inv-order-2",
                    author="user",
                    role="user",
                    text="Second question checks append order.",
                ),
                _text_event(
                    "multi_turn_append_order",
                    3,
                    invocation_id="inv-order-2",
                    author="assistant",
                    role="model",
                    text="Second answer follows the second question.",
                ),
                _text_event(
                    "multi_turn_append_order",
                    4,
                    invocation_id="inv-order-3",
                    author="user",
                    role="user",
                    text="Third question closes the order test.",
                ),
                _text_event(
                    "multi_turn_append_order",
                    5,
                    invocation_id="inv-order-3",
                    author="assistant",
                    role="model",
                    text="Third answer is last in the replay.",
                ),
            ],
            memory_queries=[],
            summary_points=[],
            description="Three user/assistant turns verify stable append ordering.",
        ),
        ReplayCase(
            name="tool_call_roundtrip",
            app_name="replay-app",
            user_id="user-tool",
            session_id="session-003",
            initial_state={},
            events=[
                _text_event(
                    "tool_call_roundtrip",
                    0,
                    invocation_id="inv-tool-1",
                    author="user",
                    role="user",
                    text="What is the weather in Beijing?",
                ),
                EventSpec(
                    event_id="tool_call_roundtrip-event-01",
                    invocation_id="inv-tool-1",
                    author="assistant",
                    role="model",
                    text=None,
                    function_call={
                        "id": "call-weather-1",
                        "name": "get_weather",
                        "args": {
                            "city": "Beijing",
                            "unit": "celsius",
                        },
                    },
                    function_response=None,
                    state_delta=None,
                    branch="weather.main",
                    tag="tool-call",
                    filter_key="weather",
                ),
                EventSpec(
                    event_id="tool_call_roundtrip-event-02",
                    invocation_id="inv-tool-1",
                    author="tool",
                    role="tool",
                    text=None,
                    function_call=None,
                    function_response={
                        "id": "call-weather-1",
                        "name": "get_weather",
                        "response": {
                            "temperature": 25,
                            "condition": "sunny",
                        },
                    },
                    state_delta=None,
                    branch="weather.main",
                    tag="tool-response",
                    filter_key="weather",
                ),
                _text_event(
                    "tool_call_roundtrip",
                    3,
                    invocation_id="inv-tool-1",
                    author="assistant",
                    role="model",
                    text="Beijing is sunny and 25 celsius.",
                    branch="weather.main",
                    tag="final",
                    filter_key="weather",
                ),
            ],
            memory_queries=[
                MemoryQuerySpec(
                    key=None,
                    query="sunny Beijing",
                    limit=10,
                    expected_text_fragments=["Beijing is sunny and 25 celsius."],
                )
            ],
            summary_points=[],
            description="Tool call and function response payloads survive normalization.",
        ),
        ReplayCase(
            name="scoped_state_overwrite",
            app_name="replay-app",
            user_id="user-state",
            session_id="session-004",
            initial_state={
                "app:region": "global",
                "user:tier": "bronze",
                "counter": 0,
            },
            events=[
                _text_event(
                    "scoped_state_overwrite",
                    0,
                    invocation_id="inv-state-1",
                    author="user",
                    role="user",
                    text="Start scoped state test.",
                    state_delta={
                        "counter": 1,
                        "temp:trace_id": "trace-1",
                    },
                ),
                _text_event(
                    "scoped_state_overwrite",
                    1,
                    invocation_id="inv-state-1",
                    author="assistant",
                    role="model",
                    text="Region set to north and tier set to silver.",
                    state_delta={
                        "app:region": "north",
                        "user:tier": "silver",
                        "counter": 2,
                    },
                ),
                _text_event(
                    "scoped_state_overwrite",
                    2,
                    invocation_id="inv-state-2",
                    author="assistant",
                    role="model",
                    text="Preference saved and tier promoted to gold.",
                    state_delta={
                        "preference": "quiet",
                        "user:tier": "gold",
                        "temp:trace_id": "trace-2",
                    },
                ),
            ],
            memory_queries=[],
            summary_points=[],
            description="App/user/session state overwrites are merged; temp state is not persisted.",
        ),
        ReplayCase(
            name="memory_preference_search",
            app_name="replay-app",
            user_id="user-memory",
            session_id="session-005",
            initial_state={},
            events=[
                _text_event(
                    "memory_preference_search",
                    0,
                    invocation_id="inv-memory-1",
                    author="user",
                    role="user",
                    text="I prefer tea in the morning.",
                ),
                _text_event(
                    "memory_preference_search",
                    1,
                    invocation_id="inv-memory-1",
                    author="assistant",
                    role="model",
                    text="I will remember that you prefer tea.",
                ),
                _text_event(
                    "memory_preference_search",
                    2,
                    invocation_id="inv-memory-2",
                    author="user",
                    role="user",
                    text="I enjoy hiking on weekends.",
                ),
                _text_event(
                    "memory_preference_search",
                    3,
                    invocation_id="inv-memory-2",
                    author="assistant",
                    role="model",
                    text="Hiking preference noted for weekends.",
                ),
                _text_event(
                    "memory_preference_search",
                    4,
                    invocation_id="inv-memory-3",
                    author="user",
                    role="user",
                    text="Please suggest vegetarian food.",
                ),
                _text_event(
                    "memory_preference_search",
                    5,
                    invocation_id="inv-memory-3",
                    author="assistant",
                    role="model",
                    text="Vegetarian food options will be prioritized.",
                ),
            ],
            memory_queries=[
                MemoryQuerySpec(key=None, query="tea", limit=10, expected_text_fragments=["prefer tea"]),
                MemoryQuerySpec(key=None, query="hiking", limit=10, expected_text_fragments=["hiking on weekends"]),
                MemoryQuerySpec(
                    key=None,
                    query="vegetarian",
                    limit=10,
                    expected_text_fragments=["vegetarian food"],
                ),
            ],
            summary_points=[],
            description="Memory search returns deterministic preference text for the session save key.",
        ),
        ReplayCase(
            name="memory_multi_session_isolation",
            app_name="replay-app",
            user_id="user-isolation-a",
            session_id="session-006-a",
            initial_state={},
            events=[
                _text_event(
                    "memory_multi_session_isolation",
                    0,
                    invocation_id="inv-isolation-a-1",
                    author="user",
                    role="user",
                    text="User A likes jasmine tea and hiking near lakes.",
                ),
                _text_event(
                    "memory_multi_session_isolation",
                    1,
                    invocation_id="inv-isolation-a-1",
                    author="assistant",
                    role="model",
                    text="I will remember jasmine tea and lake hiking for User A.",
                ),
            ],
            memory_queries=[
                MemoryQuerySpec(
                    key=None,
                    query="jasmine hiking",
                    limit=10,
                    expected_text_fragments=["jasmine tea", "lake hiking"],
                )
            ],
            summary_points=[],
            description="A second user's stored memory must not leak into user A search results.",
        ),
        ReplayCase(
            name="summary_generation",
            app_name="replay-app",
            user_id="user-summary",
            session_id="session-007",
            initial_state={},
            events=[
                _text_event(
                    "summary_generation",
                    0,
                    invocation_id="inv-summary-1",
                    author="user",
                    role="user",
                    text="Help me plan a trip to Shanghai.",
                ),
                _text_event(
                    "summary_generation",
                    1,
                    invocation_id="inv-summary-1",
                    author="assistant",
                    role="model",
                    text="Sure! When would you like to go?",
                ),
                _text_event(
                    "summary_generation",
                    2,
                    invocation_id="inv-summary-2",
                    author="user",
                    role="user",
                    text="I want museums, vegetarian food, and tea houses.",
                ),
                _text_event(
                    "summary_generation",
                    3,
                    invocation_id="inv-summary-2",
                    author="assistant",
                    role="model",
                    text="I will include museums, vegetarian restaurants, and tea houses.",
                ),
                _text_event(
                    "summary_generation",
                    4,
                    invocation_id="inv-summary-3",
                    author="user",
                    role="user",
                    text="Budget is mid range and I prefer metro travel.",
                ),
                _text_event(
                    "summary_generation",
                    5,
                    invocation_id="inv-summary-3",
                    author="assistant",
                    role="model",
                    text="I will keep it mid range with metro routes.",
                ),
                _text_event(
                    "summary_generation",
                    6,
                    invocation_id="inv-summary-4",
                    author="user",
                    role="user",
                    text="Make it a three day plan with one quiet evening.",
                ),
                _text_event(
                    "summary_generation",
                    7,
                    invocation_id="inv-summary-4",
                    author="assistant",
                    role="model",
                    text="I will create a three day Shanghai plan with a quiet evening.",
                ),
            ],
            memory_queries=[],
            summary_points=[7],
            description="Manual summary creation yields summary text, event flag, and manager metadata.",
        ),
        ReplayCase(
            name="summary_update_overwrite",
            app_name="replay-app",
            user_id="user-summary-update",
            session_id="session-008",
            initial_state={},
            events=[
                _text_event(
                    "summary_update_overwrite",
                    0,
                    invocation_id="inv-summary-update-1",
                    author="user",
                    role="user",
                    text="Plan a product launch for replay.",
                ),
                _text_event(
                    "summary_update_overwrite",
                    1,
                    invocation_id="inv-summary-update-1",
                    author="assistant",
                    role="model",
                    text="We need goals, audience, and timing.",
                ),
                _text_event(
                    "summary_update_overwrite",
                    2,
                    invocation_id="inv-summary-update-2",
                    author="user",
                    role="user",
                    text="Audience is developers and operators.",
                ),
                _text_event(
                    "summary_update_overwrite",
                    3,
                    invocation_id="inv-summary-update-2",
                    author="assistant",
                    role="model",
                    text="I will target developers and operators.",
                ),
                _text_event(
                    "summary_update_overwrite",
                    4,
                    invocation_id="inv-summary-update-3",
                    author="user",
                    role="user",
                    text="Add a release checklist and owner names.",
                ),
                _text_event(
                    "summary_update_overwrite",
                    5,
                    invocation_id="inv-summary-update-3",
                    author="assistant",
                    role="model",
                    text="Checklist and owners are now included.",
                ),
            ],
            memory_queries=[],
            summary_points=[3, 5],
            description="A later summary overwrites the cached summary for the same session.",
        ),
        ReplayCase(
            name="summary_with_event_truncation",
            app_name="replay-app",
            user_id="user-summary-truncation",
            session_id="session-009",
            initial_state={},
            events=[
                _text_event(
                    "summary_with_event_truncation",
                    0,
                    invocation_id="inv-summary-truncation-1",
                    author="user",
                    role="user",
                    text="Help me plan a trip to Shanghai.",
                ),
                _text_event(
                    "summary_with_event_truncation",
                    1,
                    invocation_id="inv-summary-truncation-1",
                    author="assistant",
                    role="model",
                    text="Sure! When would you like to go?",
                ),
                _text_event(
                    "summary_with_event_truncation",
                    2,
                    invocation_id="inv-summary-truncation-2",
                    author="user",
                    role="user",
                    text="I prefer spring with museums.",
                ),
                _text_event(
                    "summary_with_event_truncation",
                    3,
                    invocation_id="inv-summary-truncation-2",
                    author="assistant",
                    role="model",
                    text="Spring museums are good for Shanghai.",
                ),
                _text_event(
                    "summary_with_event_truncation",
                    4,
                    invocation_id="inv-summary-truncation-3",
                    author="user",
                    role="user",
                    text="Keep the last metro rides simple.",
                ),
                _text_event(
                    "summary_with_event_truncation",
                    5,
                    invocation_id="inv-summary-truncation-3",
                    author="assistant",
                    role="model",
                    text="I will keep recent metro details active.",
                ),
                _text_event(
                    "summary_with_event_truncation",
                    6,
                    invocation_id="inv-summary-truncation-4",
                    author="user",
                    role="user",
                    text="Also add a ferry ride after the summary.",
                ),
            ],
            memory_queries=[],
            summary_points=[5],
            description="Summary compression keeps historical events and then appends a new event.",
        ),
        ReplayCase(
            name="duplicate_or_error_recovery",
            app_name="replay-app",
            user_id="user-duplicate",
            session_id="session-010",
            initial_state={},
            events=[
                _text_event(
                    "duplicate_or_error_recovery",
                    0,
                    invocation_id="inv-duplicate-1",
                    author="user",
                    role="user",
                    text="Duplicate content check begins.",
                ),
                _text_event(
                    "duplicate_or_error_recovery",
                    1,
                    invocation_id="inv-duplicate-1",
                    author="assistant",
                    role="model",
                    text="Same content may repeat.",
                ),
                _text_event(
                    "duplicate_or_error_recovery",
                    2,
                    invocation_id="inv-duplicate-2",
                    author="assistant",
                    role="model",
                    text="Same content may repeat.",
                ),
                _text_event(
                    "duplicate_or_error_recovery",
                    3,
                    invocation_id="inv-duplicate-3",
                    author="assistant",
                    role="model",
                    text="Transient retry error before recovery.",
                    tag="retry-error",
                    filter_key="retry",
                    error_code="RETRYABLE_BACKEND_ERROR",
                    error_message="Simulated retry failure before recovery.",
                ),
                _text_event(
                    "duplicate_or_error_recovery",
                    4,
                    invocation_id="inv-duplicate-4",
                    author="assistant",
                    role="model",
                    text="Recovery succeeded after retry.",
                    tag="retry-recovery",
                    filter_key="retry",
                ),
            ],
            memory_queries=[
                MemoryQuerySpec(
                    key=None,
                    query="Same retry recovery",
                    limit=10,
                    expected_text_fragments=["Same content may repeat.", "Recovery succeeded after retry."],
                )
            ],
            summary_points=[],
            description=(
                "Duplicate content with distinct ids plus an error/retry/recovery event sequence "
                "is captured as the backend stores it."
            ),
        ),
    ]
