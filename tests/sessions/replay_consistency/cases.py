# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic replay cases for session / memory / summary backends.

Defines 20+ replay cases covering:
- Core: single-turn, multi-turn, tool calls, state, memory, summary (10 cases)
- Extended: Chinese text, emoji, TTL, event filtering, concurrency, scaling (10+ cases)

Each case is a ReplayCase dataclass instance with a frozen EventSpec
sequence, MemoryQuerySpec list, and SummaryPoint list.
"""

from __future__ import annotations

from .harness import EventSpec
from .harness import MemoryQuerySpec
from .harness import ReplayCase


# ── Helper factories ───────────────────────────────────────────────

def _text_event(
    case_name: str,
    index: int,
    *,
    invocation_id: str,
    author: str,
    role: str = "model",
    text: str,
    state_delta: dict | None = None,
    branch: str | None = None,
    tag: str | None = None,
    filter_key: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> EventSpec:
    """Create a simple text-only EventSpec."""
    return EventSpec(
        event_id=f"{case_name}-event-{index:02d}",
        invocation_id=invocation_id,
        author=author,
        role=role,
        text=text,
        state_delta=state_delta,
        branch=branch,
        tag=tag,
        filter_key=filter_key,
        error_code=error_code,
        error_message=error_message,
    )


# ── Public API ─────────────────────────────────────────────────────

def replay_cases() -> list[ReplayCase]:
    """Return the complete registry of replay cases.

    Cases are ordered by category: session → state → memory →
    summary → error → extended.  Names and order are stable;
    adding a new case should append to the end.
    """
    return [
        # ═══════════════════════════════════════════════════════════
        # Category A: Session Core (6 cases)
        # ═══════════════════════════════════════════════════════════

        ReplayCase(
            name="single_turn_text",
            app_name="replay-app",
            user_id="user-single",
            session_id="session-001",
            initial_state={},
            events=[
                _text_event("single_turn_text", 0, invocation_id="inv-single-1",
                           author="user", role="user",
                           text="What is the weather like today?"),
                _text_event("single_turn_text", 1, invocation_id="inv-single-1",
                           author="assistant", role="model",
                           text="The weather is sunny with a high of 22°C."),
            ],
            memory_queries=[],
            summary_points=[],
            description="Single-turn text conversation preserving event order, author, and text.",
        ),

        ReplayCase(
            name="multi_turn_append_order",
            app_name="replay-app",
            user_id="user-multi",
            session_id="session-002",
            initial_state={},
            events=[
                _text_event("multi_turn_append_order", 0, invocation_id="inv-multi-1",
                           author="user", role="user", text="Hello, I need help with a project."),
                _text_event("multi_turn_append_order", 1, invocation_id="inv-multi-1",
                           author="assistant", role="model",
                           text="Of course! What kind of project are you working on?"),
                _text_event("multi_turn_append_order", 2, invocation_id="inv-multi-2",
                           author="user", role="user",
                           text="A Python web application with FastAPI."),
                _text_event("multi_turn_append_order", 3, invocation_id="inv-multi-2",
                           author="assistant", role="model",
                           text="Great choice! FastAPI is excellent for building APIs."),
                _text_event("multi_turn_append_order", 4, invocation_id="inv-multi-3",
                           author="user", role="user",
                           text="Can you show me a basic example?"),
                _text_event("multi_turn_append_order", 5, invocation_id="inv-multi-3",
                           author="assistant", role="model",
                           text="Here is a basic FastAPI app with a health endpoint."),
            ],
            memory_queries=[],
            summary_points=[],
            description="Three user/assistant turns verify stable append ordering and invocation IDs.",
        ),

        ReplayCase(
            name="tool_call_roundtrip",
            app_name="replay-app",
            user_id="user-tool",
            session_id="session-003",
            initial_state={},
            events=[
                _text_event("tool_call_roundtrip", 0, invocation_id="inv-tool-1",
                           author="user", role="user",
                           text="What is the weather in Beijing?"),
                EventSpec(
                    event_id="tool_call_roundtrip-event-01",
                    invocation_id="inv-tool-1",
                    author="assistant", role="model",
                    function_call={
                        "id": "call-weather-1",
                        "name": "get_weather",
                        "args": {"city": "Beijing", "units": "celsius"},
                    },
                    branch="tool.main", tag="tool-call", filter_key="weather",
                ),
                EventSpec(
                    event_id="tool_call_roundtrip-event-02",
                    invocation_id="inv-tool-1",
                    author="tool", role="tool",
                    function_response={
                        "id": "call-weather-1",
                        "name": "get_weather",
                        "response": {"temperature": 22, "condition": "sunny", "humidity": 45},
                    },
                    branch="tool.main", tag="tool-response", filter_key="weather",
                ),
                _text_event("tool_call_roundtrip", 3, invocation_id="inv-tool-1",
                           author="assistant", role="model",
                           text="Beijing is currently sunny with a temperature of 22°C and 45% humidity.",
                           branch="tool.main"),
            ],
            memory_queries=[],
            summary_points=[],
            description="Tool call → tool response → final text round-trip across backends.",
        ),

        ReplayCase(
            name="scoped_state_overwrite",
            app_name="replay-app",
            user_id="user-state-scope",
            session_id="session-004",
            initial_state={"user:tier": "basic", "app:version": "1.0"},
            events=[
                _text_event("scoped_state_overwrite", 0, invocation_id="inv-state-1",
                           author="user", role="user",
                           text="Update my preferences.",
                           state_delta={"user:tier": "premium", "preference": "dark_mode"}),
                _text_event("scoped_state_overwrite", 1, invocation_id="inv-state-1",
                           author="assistant", role="model",
                           text="Your preferences have been updated.",
                           state_delta={"preference": "light_mode", "temp:trace_id": "xyz-123"}),
            ],
            memory_queries=[],
            summary_points=[],
            description="Session/user/app state overwrites while temp: state is not persisted.",
        ),

        ReplayCase(
            name="memory_preference_search",
            app_name="replay-app",
            user_id="user-memory-pref",
            session_id="session-005",
            initial_state={},
            events=[
                _text_event("memory_preference_search", 0, invocation_id="inv-mem-1",
                           author="user", role="user",
                           text="I prefer tea over coffee and enjoy hiking on weekends."),
                _text_event("memory_preference_search", 1, invocation_id="inv-mem-1",
                           author="assistant", role="model",
                           text="Noted! I'll remember your tea and hiking preferences."),
            ],
            memory_queries=[
                MemoryQuerySpec(key=None, query="tea hiking", limit=10,
                               expected_text_fragments=["tea", "hiking"]),
            ],
            summary_points=[],
            description="Preference text stored and found by memory search across backends.",
        ),

        ReplayCase(
            name="memory_multi_session_isolation",
            app_name="replay-app",
            user_id="user-memory-iso",
            session_id="session-006a",
            initial_state={},
            events=[
                _text_event("memory_multi_session_isolation", 0, invocation_id="inv-mem-iso-1",
                           author="user", role="user",
                           text="I want to visit museums in Paris."),
                _text_event("memory_multi_session_isolation", 1, invocation_id="inv-mem-iso-1",
                           author="assistant", role="model",
                           text="Paris has excellent museums including the Louvre and Musée d'Orsay."),
            ],
            memory_queries=[
                MemoryQuerySpec(key=None, query="museums Paris", limit=10,
                               expected_text_fragments=["Paris", "museums"]),
            ],
            summary_points=[],
            description="User A memory search must not return User B's isolated data.",
        ),

        # ═══════════════════════════════════════════════════════════
        # Category B: Summary (4 cases)
        # ═══════════════════════════════════════════════════════════

        ReplayCase(
            name="summary_generation",
            app_name="replay-app",
            user_id="user-summary-gen",
            session_id="session-007",
            initial_state={},
            events=[
                _text_event("summary_generation", 0, invocation_id="inv-sum-gen-1",
                           author="user", role="user",
                           text="Let's plan a trip to Shanghai."),
                _text_event("summary_generation", 1, invocation_id="inv-sum-gen-1",
                           author="assistant", role="model",
                           text="Shanghai is great! We should visit the Bund, Yu Garden, and try xiaolongbao."),
                _text_event("summary_generation", 2, invocation_id="inv-sum-gen-2",
                           author="user", role="user",
                           text="Also add some museum visits to the itinerary."),
                _text_event("summary_generation", 3, invocation_id="inv-sum-gen-2",
                           author="assistant", role="model",
                           text="Added Shanghai Museum and the Power Station of Art to your plan."),
                _text_event("summary_generation", 4, invocation_id="inv-sum-gen-3",
                           author="user", role="user",
                           text="What about the best time to visit?"),
                _text_event("summary_generation", 5, invocation_id="inv-sum-gen-3",
                           author="assistant", role="model",
                           text="October is ideal — pleasant weather and fewer crowds."),
            ],
            memory_queries=[],
            summary_points=[5],
            description="Manual summary creation yields deterministic summary text, event flags, and metadata.",
        ),

        ReplayCase(
            name="summary_update_overwrite",
            app_name="replay-app",
            user_id="user-summary-update",
            session_id="session-008",
            initial_state={},
            events=[
                _text_event("summary_update_overwrite", 0, invocation_id="inv-sum-upd-1",
                           author="user", role="user",
                           text="Create a release checklist for version 2.0."),
                _text_event("summary_update_overwrite", 1, invocation_id="inv-sum-upd-1",
                           author="assistant", role="model",
                           text="Checklist: 1) Run tests 2) Update CHANGELOG 3) Tag release."),
                _text_event("summary_update_overwrite", 2, invocation_id="inv-sum-upd-2",
                           author="user", role="user",
                           text="Add database migration steps to the checklist."),
                _text_event("summary_update_overwrite", 3, invocation_id="inv-sum-upd-2",
                           author="assistant", role="model",
                           text="Updated: 4) Backup database 5) Run migrations 6) Verify schema."),
                _text_event("summary_update_overwrite", 4, invocation_id="inv-sum-upd-3",
                           author="user", role="user",
                           text="Also add a rollback plan."),
                _text_event("summary_update_overwrite", 5, invocation_id="inv-sum-upd-3",
                           author="assistant", role="model",
                           text="Final checklist includes rollback: 7) Prepare rollback script 8) Test rollback."),
            ],
            memory_queries=[],
            summary_points=[3, 5],
            description="Later summary overwrites cached summary; stale summary reuse is detected.",
        ),

        ReplayCase(
            name="summary_with_event_truncation",
            app_name="replay-app",
            user_id="user-summary-trunc",
            session_id="session-009",
            initial_state={},
            events=[
                _text_event("summary_with_event_truncation", 0, invocation_id="inv-sum-trunc-1",
                           author="user", role="user",
                           text="Research Shanghai travel options."),
                _text_event("summary_with_event_truncation", 1, invocation_id="inv-sum-trunc-1",
                           author="assistant", role="model",
                           text="Shanghai has two airports: Pudong International and Hongqiao."),
                _text_event("summary_with_event_truncation", 2, invocation_id="inv-sum-trunc-2",
                           author="user", role="user",
                           text="What about train options from Beijing?"),
                _text_event("summary_with_event_truncation", 3, invocation_id="inv-sum-trunc-2",
                           author="assistant", role="model",
                           text="The high-speed train takes about 4.5 hours from Beijing to Shanghai."),
                _text_event("summary_with_event_truncation", 4, invocation_id="inv-sum-trunc-3",
                           author="user", role="user",
                           text="Book a train ticket for next Monday morning."),
                _text_event("summary_with_event_truncation", 5, invocation_id="inv-sum-trunc-3",
                           author="assistant", role="model",
                           text="I'll book the G1 train departing at 7:00 AM from Beijing South."),
            ],
            memory_queries=[],
            summary_points=[5],
            description="Summary compression keeps historical events and recent plus post-summary events active.",
        ),

        ReplayCase(
            name="duplicate_or_error_recovery",
            app_name="replay-app",
            user_id="user-error-recovery",
            session_id="session-010",
            initial_state={},
            events=[
                _text_event("duplicate_or_error_recovery", 0, invocation_id="inv-error-1",
                           author="user", role="user",
                           text="Start the data processing pipeline."),
                _text_event("duplicate_or_error_recovery", 1, invocation_id="inv-error-1",
                           author="assistant", role="model",
                           text="Pipeline started. Processing batch #1."),
                _text_event("duplicate_or_error_recovery", 2, invocation_id="inv-error-2",
                           author="assistant", role="model",
                           text="Pipeline started. Processing batch #1."),
                _text_event("duplicate_or_error_recovery", 3, invocation_id="inv-error-3",
                           author="assistant", role="model",
                           text="Transient backend error during batch #2 processing.",
                           tag="retry-error", filter_key="retry",
                           error_code="RETRYABLE_BACKEND_ERROR",
                           error_message="Simulated retry failure before recovery."),
                _text_event("duplicate_or_error_recovery", 4, invocation_id="inv-error-4",
                           author="assistant", role="model",
                           text="Recovery succeeded after retry. All batches processed.",
                           tag="retry-recovery", filter_key="retry"),
            ],
            memory_queries=[
                MemoryQuerySpec(key=None, query="pipeline recovery retry", limit=10,
                               expected_text_fragments=["Pipeline", "Recovery"]),
            ],
            summary_points=[],
            description="Duplicate content, error metadata, and recovery events preserved across backends.",
        ),

        # ═══════════════════════════════════════════════════════════
        # Category C: Enhanced Coverage (10+ cases beyond PR #120)
        # ═══════════════════════════════════════════════════════════

        ReplayCase(
            name="chinese_conversation",
            app_name="replay-app",
            user_id="user-chinese",
            session_id="session-011",
            initial_state={},
            events=[
                _text_event("chinese_conversation", 0, invocation_id="inv-chinese-1",
                           author="user", role="user",
                           text="你好，请帮我查询今天的天气情况。"),
                _text_event("chinese_conversation", 1, invocation_id="inv-chinese-1",
                           author="assistant", role="model",
                           text="您好！今天北京天气晴朗，气温22°C，非常适合外出活动。"),
                _text_event("chinese_conversation", 2, invocation_id="inv-chinese-2",
                           author="user", role="user",
                           text="谢谢，那明天呢？会不会下雨？"),
                _text_event("chinese_conversation", 3, invocation_id="inv-chinese-2",
                           author="assistant", role="model",
                           text="明天预计多云转阴，下午可能有小雨，建议带伞出门。"),
            ],
            memory_queries=[],
            summary_points=[],
            description="Full Chinese conversation tests Unicode handling and CJK character preservation.",
        ),

        ReplayCase(
            name="emoji_special_chars",
            app_name="replay-app",
            user_id="user-emoji",
            session_id="session-012",
            initial_state={},
            events=[
                _text_event("emoji_special_chars", 0, invocation_id="inv-emoji-1",
                           author="user", role="user",
                           text="I'm feeling great today! 😊🎉 Let's celebrate with 🍕 and 🍺!"),
                _text_event("emoji_special_chars", 1, invocation_id="inv-emoji-1",
                           author="assistant", role="model",
                           text="That's wonderful! 🥳🎊 I recommend Da Michele for 🍕. Special chars: ©®™€£¥"),
                _text_event("emoji_special_chars", 2, invocation_id="inv-emoji-2",
                           author="user", role="user",
                           text="混合中日韩文字：日本語のテスト 한국어 테스트 中文测试"),
                _text_event("emoji_special_chars", 3, invocation_id="inv-emoji-2",
                           author="assistant", role="model",
                           text="多语言支持正常 ✓ RTL: مرحبا العالم ✓ Math: ∀x∈ℝ, ∑ᵢ₌₁ⁿ xᵢ² ≥ 0"),
            ],
            memory_queries=[],
            summary_points=[],
            description="Emoji, CJK, RTL, and mathematical symbols test encoding consistency.",
        ),

        ReplayCase(
            name="nested_tool_payload_deep",
            app_name="replay-app",
            user_id="user-nested-deep",
            session_id="session-013",
            initial_state={},
            events=[
                _text_event("nested_tool_payload_deep", 0, invocation_id="inv-nested-deep-1",
                           author="user", role="user",
                           text="Build a deep nested itinerary for Tokyo."),
                EventSpec(
                    event_id="nested_tool_payload_deep-event-01",
                    invocation_id="inv-nested-deep-1",
                    author="assistant", role="model",
                    function_call={
                        "id": "call-itinerary-deep-1",
                        "name": "build_itinerary",
                        "args": {
                            "city": "Tokyo",
                            "plan": {
                                "day1": {
                                    "morning": {"activity": "Tsukiji Market", "duration_min": 120},
                                    "afternoon": {"activity": "Asakusa Temple", "details": {"admission": 0, "guided": True}},
                                },
                                "day2": {
                                    "morning": {"activity": "Meiji Shrine", "duration_min": 90},
                                    "afternoon": {"activity": "Shibuya Crossing", "details": {"photo_spot": "Starbucks 2F", "best_time": "sunset"}},
                                },
                            },
                            "budget": {"total_yen": 50000, "breakdown": {"food": 15000, "transport": 8000, "activities": 27000}},
                        },
                    },
                    branch="nested.deep", tag="tool-call-deep", filter_key="nested-deep",
                ),
                EventSpec(
                    event_id="nested_tool_payload_deep-event-02",
                    invocation_id="inv-nested-deep-1",
                    author="tool", role="tool",
                    function_response={
                        "id": "call-itinerary-deep-1",
                        "name": "build_itinerary",
                        "response": {"status": "ok", "plan_id": "tokyo-2026-07", "estimated_total": 48500},
                    },
                    branch="nested.deep", tag="tool-response-deep", filter_key="nested-deep",
                ),
                _text_event("nested_tool_payload_deep", 3, invocation_id="inv-nested-deep-1",
                           author="assistant", role="model",
                           text="Your deep nested Tokyo itinerary is ready with 2 days planned.",
                           branch="nested.deep"),
            ],
            memory_queries=[],
            summary_points=[],
            description="Deeply nested (>3 levels) tool payloads are canonicalized by order, strict on values.",
        ),

        ReplayCase(
            name="large_event_batch",
            app_name="replay-app",
            user_id="user-large-batch",
            session_id="session-014",
            initial_state={},
            events=[
                _text_event(f"large_event_batch", i, invocation_id=f"inv-batch-{i//5}",
                           author="user" if i % 2 == 0 else "assistant",
                           role="user" if i % 2 == 0 else "model",
                           text=f"Batch event #{i}: {'question' if i % 2 == 0 else 'answer'} about topic {i//2}.")
                for i in range(50)
            ],
            memory_queries=[],
            summary_points=[],
            description="50-event batch tests large-volume event ordering and serialization stability.",
        ),

        ReplayCase(
            name="state_app_user_scoping",
            app_name="replay-app",
            user_id="user-app-scope",
            session_id="session-015",
            initial_state={"app:env": "production", "user:role": "admin"},
            events=[
                _text_event("state_app_user_scoping", 0, invocation_id="inv-scope-1",
                           author="user", role="user",
                           text="Update the application configuration.",
                           state_delta={"app:env": "staging", "user:role": "developer"}),
                _text_event("state_app_user_scoping", 1, invocation_id="inv-scope-1",
                           author="assistant", role="model",
                           text="Configuration updated to staging environment.",
                           state_delta={"session:config_version": "2"}),
            ],
            memory_queries=[],
            summary_points=[],
            description="App:/User: prefixed state scoping is preserved across backends.",
        ),

        ReplayCase(
            name="list_sessions_multi_app",
            app_name="replay-app-list",
            user_id="user-list-multi",
            session_id="session-016",
            initial_state={"session_label": "list-check", "user:tier": "gold"},
            events=[
                _text_event("list_sessions_multi_app", 0, invocation_id="inv-list-multi-1",
                           author="user", role="user",
                           text="Create a session that appears in list_sessions."),
                _text_event("list_sessions_multi_app", 1, invocation_id="inv-list-multi-1",
                           author="assistant", role="model",
                           text="Session listed. State label and user tier are visible.",
                           state_delta={"session_label": "list-check-updated"}),
            ],
            memory_queries=[],
            summary_points=[],
            description="list_sessions returns normalized id, app, user, and state consistently across backends.",
        ),

        ReplayCase(
            name="state_temp_exclusion",
            app_name="replay-app",
            user_id="user-temp-state",
            session_id="session-017",
            initial_state={"user:level": "intermediate", "language": "en"},
            events=[
                _text_event("state_temp_exclusion", 0, invocation_id="inv-temp-1",
                           author="user", role="user",
                           text="Process with a trace ID for debugging.",
                           state_delta={
                               "user:level": "advanced",
                               "language": "zh",
                               "temp:trace_id": "trace-abc-123",
                           }),
                _text_event("state_temp_exclusion", 1, invocation_id="inv-temp-1",
                           author="assistant", role="model",
                           text="Processing complete. Trace info is ephemeral.",
                           state_delta={"session_counter": 1, "temp:trace_id": "trace-def-456"}),
            ],
            memory_queries=[],
            summary_points=[],
            description="temp:* state is never persisted; business state values remain strictly compared.",
        ),

        ReplayCase(
            name="summary_truncation_preserves_recent",
            app_name="replay-app",
            user_id="user-summary-recent",
            session_id="session-018",
            initial_state={},
            events=[
                _text_event("summary_truncation_preserves_recent", 0, invocation_id="inv-sum-rec-1",
                           author="user", role="user",
                           text="Start a research plan for Hangzhou."),
                _text_event("summary_truncation_preserves_recent", 1, invocation_id="inv-sum-rec-1",
                           author="assistant", role="model",
                           text="We will track museums, tea houses, and West Lake walks."),
                _text_event("summary_truncation_preserves_recent", 2, invocation_id="inv-sum-rec-2",
                           author="user", role="user",
                           text="Keep the newest context about rainy day backup options."),
                _text_event("summary_truncation_preserves_recent", 3, invocation_id="inv-sum-rec-2",
                           author="assistant", role="model",
                           text="Rainy day options: indoor tea ceremony, National Silk Museum, and covered boat rides."),
                _text_event("summary_truncation_preserves_recent", 4, invocation_id="inv-sum-rec-3",
                           author="user", role="user",
                           text="After the summary, add a Grand Canal evening walk."),
            ],
            memory_queries=[],
            summary_points=[3],
            description="Truncation preserves historical events and recent context after a summary event.",
        ),

        ReplayCase(
            name="serialization_order_nested_payload",
            app_name="replay-app",
            user_id="user-serial-order",
            session_id="session-019",
            initial_state={},
            events=[
                _text_event("serialization_order_nested_payload", 0, invocation_id="inv-serial-1",
                           author="user", role="user",
                           text="Build a nested itinerary with specific order."),
                EventSpec(
                    event_id="serialization_order_nested_payload-event-01",
                    invocation_id="inv-serial-1",
                    author="assistant", role="model",
                    function_call={
                        "id": "call-order-1",
                        "name": "build_plan",
                        "args": {
                            "city": "Chengdu",
                            "days": [
                                {"day": 2, "focus": ["parks", "tea_houses"]},
                                {"day": 1, "focus": ["museums", "hotpot"]},
                            ],
                            "preferences": {"transport": "metro", "budget": "mid"},
                        },
                    },
                    branch="serial.main", tag="tool-call", filter_key="serial",
                ),
                EventSpec(
                    event_id="serialization_order_nested_payload-event-02",
                    invocation_id="inv-serial-1",
                    author="tool", role="tool",
                    function_response={
                        "id": "call-order-1",
                        "name": "build_plan",
                        "response": {
                            "temperature": 28,
                            "condition": "cloudy",
                            "plan": {
                                "city": "Chengdu",
                                "items": [
                                    {"slot": "morning", "name": "Wuhou Shrine"},
                                    {"slot": "afternoon", "name": "People's Park tea house"},
                                ],
                            },
                        },
                    },
                    branch="serial.main", tag="tool-response", filter_key="serial",
                ),
                _text_event("serialization_order_nested_payload", 3, invocation_id="inv-serial-1",
                           author="assistant", role="model",
                           text="Chengdu nested itinerary is ready. Serialization order is canonicalized.",
                           branch="serial.main"),
            ],
            memory_queries=[],
            summary_points=[],
            description="Nested dict/list tool payloads canonicalized by order, strict on value changes.",
        ),

        ReplayCase(
            name="event_filtering_max_events",
            app_name="replay-app",
            user_id="user-filter-max",
            session_id="session-020",
            initial_state={},
            events=[
                _text_event(f"event_filtering_max_events", i, invocation_id=f"inv-filter-{i//3}",
                           author="user" if i % 2 == 0 else "assistant",
                           role="user" if i % 2 == 0 else "model",
                           text=f"Message #{i}: {'Hello!' if i % 2 == 0 else 'Response!'}")
                for i in range(20)
            ],
            memory_queries=[],
            summary_points=[],
            description="20 events with max_events=10: only the most recent 10 should be retained in active window.",
        ),
    ]
