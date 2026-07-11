"""Replay cases for acceptance and extended consistency tests."""

from __future__ import annotations

from .replay_models import EventSpec
from .replay_models import FunctionCallSpec
from .replay_models import FunctionResponseSpec
from .replay_models import ReplayCase
from .replay_models import ReplayStep
from .replay_models import RuntimeFault
from .replay_models import RuntimeFaultOperation
from .replay_models import SnapshotMutation
from .replay_models import SnapshotMutationOperation


_PERSISTENT_BACKEND = "persistent"


_BASELINE_CASES: tuple[ReplayCase, ...] = (
    ReplayCase(
        case_id="single_turn_text",
        description="One user turn followed by one assistant text response.",
        session_id="replay-single-turn",
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Hello, what can you do?"),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I can help answer questions."),
            ),
        ),
    ),
    ReplayCase(
        case_id="multi_turn_dialogue",
        description="Multiple user and assistant turns should preserve event ordering across backends.",
        session_id="replay-multi-turn",
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Hello assistant."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Hello, how can I help?"),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember my travel plan."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will remember your travel plan."),
            ),
        ),
    ),
    ReplayCase(
        case_id="tool_call_and_response",
        description="Assistant tool call followed by tool response.",
        session_id="replay-tool-call",
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Check the Beijing weather."),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    function_calls=(
                        FunctionCallSpec(
                            name="get_weather",
                            args={"city": "Beijing"},
                            call_id="call-weather-1",
                        ),
                    ),
                ),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="user",
                    function_responses=(
                        FunctionResponseSpec(
                            name="get_weather",
                            response={"temperature": "25C", "condition": "Sunny"},
                            call_id="call-weather-1",
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReplayCase(
        case_id="state_and_memory_roundtrip",
        description="Repeated session state updates should preserve overwrite semantics before memory persistence.",
        session_id="replay-memory-state",
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer tea."),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Actually update that to green tea."),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Updated. You now prefer green tea.",
                    state_delta={"preference": "green tea", "drink_temperature": "hot"},
                ),
            ),
            ReplayStep.store_memory(),
            ReplayStep.search_memory(name="preference_search", query="green tea", limit=5),
        ),
    ),
    ReplayCase(
        case_id="summary_compaction_with_history",
        description="Deterministic summary compaction keeps recent events and stores historical events.",
        session_id="replay-summary-history",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="I am planning a weekend trip."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Great, where would you like to go?"),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="I want to visit Hangzhou."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Hangzhou is known for West Lake."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="summary_version_rolls_forward",
        description="A second summary should replace the first one with incremented lineage metadata.",
        session_id="replay-summary-version",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Remember my project is called Atlas."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Got it, your project is Atlas."),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="It uses Redis and SQL backends."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Atlas uses Redis and SQL backends."),
            ),
            ReplayStep.create_summary(force=True),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Also note that replay consistency is critical."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will keep replay consistency in mind."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
)


_NEGATIVE_CASES: tuple[ReplayCase, ...] = (
    ReplayCase(
        case_id="summary_binding_mismatch_injection",
        description="Injected summary ownership mismatch must be reported at the exact summary field path.",
        session_id="replay-negative-summary-binding",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary.session_id",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="summary.session_id",
                value="wrong-session-id",
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please summarize my travel plan."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Sure, tell me the route."),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Shanghai to Hangzhou by train."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="That route is short and convenient."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="summary_missing_injection",
        description="Injected summary loss must be detected as a summary-level mismatch.",
        session_id="replay-negative-summary-missing",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="summary",
                value=None,
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Track my preferences for black coffee."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I noted your coffee preference."),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Also remember I dislike sugary drinks."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will avoid sugary drink suggestions."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="state_corruption_injection",
        description="Injected state corruption must surface at the exact state field path.",
        session_id="replay-negative-state-corruption",
        expected_diff_paths=("state.preference",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="state.preference",
                value="coffee",
            ),
        ),
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer tea."),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
        ),
    ),
    ReplayCase(
        case_id="summary_lineage_corruption_injection",
        description="Injected summary lineage corruption must be detected via the replaces field.",
        session_id="replay-negative-summary-lineage",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary.replaces",),
        snapshot_mutations=(
            SnapshotMutation(
                backend_name=_PERSISTENT_BACKEND,
                path="summary.replaces",
                value="wrong-summary-id",
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Remember my project codename is Northstar."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="The codename is Northstar."),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="It runs on both SQLite and Redis."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Northstar runs on SQLite and Redis."),
            ),
            ReplayStep.create_summary(force=True),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Replay consistency matters a lot."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will keep replay consistency as a priority."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="duplicate_event_runtime_fault",
        description="A duplicated event injected during replay must be detected as an event-count mismatch.",
        session_id="replay-negative-duplicate-event",
        expected_diff_paths=("session.events.length",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=2,
                operation=RuntimeFaultOperation.DUPLICATE_LAST_EVENT,
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please store this reminder."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I stored the reminder."),
            ),
        ),
    ),
    ReplayCase(
        case_id="runtime_state_corruption_fault",
        description="A runtime state corruption must be detected on the precise state path.",
        session_id="replay-negative-runtime-state",
        expected_diff_paths=("state.preference",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=2,
                operation=RuntimeFaultOperation.SET_SESSION_VALUE,
                path="state.preference",
                value="coffee",
            ),
        ),
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer tea."),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
        ),
    ),
    ReplayCase(
        case_id="runtime_summary_loss_fault",
        description="A runtime summary deletion must be detected as a missing summary.",
        session_id="replay-negative-runtime-summary-loss",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=("summary",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=5,
                operation=RuntimeFaultOperation.DELETE_SUMMARY,
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please summarize my sprint notes."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Sure, continue."),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="We fixed replay ordering bugs."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="The replay ordering bugs were fixed."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="runtime_summary_overwrite_fault",
        description="A runtime summary overwrite must be detected through lineage replacement fields.",
        session_id="replay-negative-runtime-summary-overwrite",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        expected_diff_paths=(
            "summary.replaces",
            "summary.metadata.replaces",
        ),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=8,
                operation=RuntimeFaultOperation.SET_SUMMARY_VALUE,
                path="metadata.replaces",
                value="wrong-summary-id",
            ),
        ),
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Remember the codename is Aurora."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="The codename is Aurora."),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Aurora runs on SQL and Redis."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Aurora runs on SQL and Redis."),
            ),
            ReplayStep.create_summary(force=True),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Replay correctness matters a lot."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will prioritize replay correctness."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="partial_failure_event_loss_fault",
        description="A partial failure that loses the final event but keeps state must be detected as an event-window mismatch.",
        session_id="replay-negative-partial-failure",
        expected_diff_paths=("session.events.length",),
        runtime_faults=(
            RuntimeFault(
                backend_name=_PERSISTENT_BACKEND,
                after_step=2,
                operation=RuntimeFaultOperation.DROP_LAST_EVENT_KEEP_STATE,
            ),
        ),
        steps=(
            ReplayStep.create_session(initial_state={"user_name": "alice"}),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer tea."),
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Noted. You prefer tea over coffee.",
                    state_delta={"preference": "tea"},
                ),
            ),
        ),
    ),
)


_ROBUSTNESS_CASES: tuple[ReplayCase, ...] = (
    ReplayCase(
        case_id="cross_session_memory_aggregation",
        description="Memory written by one session should remain searchable from another session under the same app/user scope.",
        session_id="replay-memory-target",
        steps=(
            ReplayStep.create_session(session_alias="source", session_id="replay-memory-source"),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember that I prefer oolong tea."),
                session_alias="source",
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Noted. You prefer oolong tea."),
                session_alias="source",
            ),
            ReplayStep.store_memory(session_alias="source"),
            ReplayStep.create_session(session_alias="default", session_id="replay-memory-target"),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="What drink did I say I prefer?"),
            ),
            ReplayStep.search_memory(
                name="cross_session_preference_search",
                query="oolong",
                limit=5,
            ),
        ),
    ),
    ReplayCase(
        case_id="restart_mid_replay_after_summary",
        description="Persistent backends should restore summary state correctly after a restart and continue replaying later turns.",
        session_id="replay-restart-summary",
        enable_summary=True,
        summary_keep_recent_count=2,
        store_historical_events=True,
        steps=(
            ReplayStep.create_session(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="I am planning a Hangzhou trip."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Great, what should I remember?"),
            ),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Please remember I need a hotel near West Lake."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will remember the hotel preference."),
            ),
            ReplayStep.create_summary(force=True),
            ReplayStep.restart_services(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="Also note that I will arrive next Friday."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Arrival next Friday is recorded."),
            ),
            ReplayStep.restart_services(),
            ReplayStep.append_event(
                EventSpec(author="user", role="user", text="I prefer morning check-in if available."),
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="I will keep the morning check-in preference."),
            ),
            ReplayStep.create_summary(force=True),
        ),
    ),
    ReplayCase(
        case_id="state_namespace_roundtrip",
        description="App, user, session, and temp state should preserve their intended visibility across sessions and restarts.",
        session_id="replay-state-namespace-b",
        steps=(
            ReplayStep.create_session(
                session_alias="writer",
                session_id="replay-state-namespace-a",
                initial_state={
                    "app:locale": "zh-CN",
                    "user:timezone": "Asia/Shanghai",
                    "temp:request_id": "req-1",
                    "draft": "first-session",
                },
            ),
            ReplayStep.append_event(
                EventSpec(
                    author="assistant",
                    role="model",
                    text="Updating shared and session state.",
                    state_delta={
                        "app:release": "2026.07",
                        "user:tone": "concise",
                        "temp:trace_id": "trace-1",
                        "draft": "writer-updated",
                    },
                ),
                session_alias="writer",
            ),
            ReplayStep.restart_services(),
            ReplayStep.create_session(
                session_alias="default",
                session_id="replay-state-namespace-b",
                initial_state={
                    "temp:request_id": "req-2",
                    "draft": "second-session",
                },
            ),
            ReplayStep.append_event(
                EventSpec(author="assistant", role="model", text="Second session should inherit shared state only."),
            ),
        ),
    ),
)


# The acceptance suite is the public, fixed set of 10 replay cases used to
# demonstrate baseline correctness and inconsistency detection.
REPLAY_ACCEPTANCE_CASES: tuple[ReplayCase, ...] = (
    _BASELINE_CASES[0],  # single_turn_text
    _BASELINE_CASES[1],  # multi_turn_dialogue
    _BASELINE_CASES[2],  # tool_call_and_response
    _BASELINE_CASES[3],  # state_and_memory_roundtrip
    _BASELINE_CASES[4],  # summary_compaction_with_history
    _BASELINE_CASES[5],  # summary_version_rolls_forward
    _NEGATIVE_CASES[0],  # summary_binding_mismatch_injection
    _NEGATIVE_CASES[1],  # summary_missing_injection
    _NEGATIVE_CASES[7],  # runtime_summary_overwrite_fault
    _NEGATIVE_CASES[8],  # partial_failure_event_loss_fault
)


# Extra cases extend coverage beyond the fixed acceptance set while reusing the
# same harness and reporting pipeline.
REPLAY_EXTRA_CASES: tuple[ReplayCase, ...] = (
    _NEGATIVE_CASES[2],  # state_corruption_injection
    _NEGATIVE_CASES[3],  # summary_lineage_corruption_injection
    _NEGATIVE_CASES[4],  # duplicate_event_runtime_fault
    _NEGATIVE_CASES[5],  # runtime_state_corruption_fault
    _NEGATIVE_CASES[6],  # runtime_summary_loss_fault
    _ROBUSTNESS_CASES[0],  # cross_session_memory_aggregation
    _ROBUSTNESS_CASES[1],  # restart_mid_replay_after_summary
    _ROBUSTNESS_CASES[2],  # state_namespace_roundtrip
)


REPLAY_ALL_CASES: tuple[ReplayCase, ...] = REPLAY_ACCEPTANCE_CASES + REPLAY_EXTRA_CASES
