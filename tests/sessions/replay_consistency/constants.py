# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Constants for replay consistency tests."""

from pathlib import Path

REPLAY_CASES_DIR = Path(__file__).parent / "replay_cases"

EXPECTED_REPLAY_CASE_NAMES = {
    "single_turn",
    "multi_turn",
    "tool_call",
    "state_update",
    "memory_store_search",
    "summary_generation_update",
    "summary_truncation",
    "recovery_partial_event",
    "duplicate_event_replay",
    "branch_metadata_replay",
}

EXPECTED_REPLAY_CASE_FILES = [
    "01_single_turn.jsonl",
    "02_multi_turn.jsonl",
    "03_tool_call.jsonl",
    "04_state_update.jsonl",
    "05_memory_store_search.jsonl",
    "06_summary_event_update.jsonl",
    "07_summary_truncation.jsonl",
    "08_recovery_partial_event.jsonl",
    "09_duplicate_event_replay.jsonl",
    "10_branch_metadata_replay.jsonl",
]

SUMMARY_TRUNCATION_ALLOWED_DIFF_REASON = (
    "InMemorySessionService keeps max_events-trimmed events in active storage instead of "
    "moving them to historical_events; SQLite stores those filtered events in historical_events."
)

SUMMARY_TRUNCATION_ALLOWED_DIFF_PATHS = {
    "events[0].author",
    "events[0].custom_metadata",
    "events[0].invocation_id",
    "events[0].is_summary",
    "events[0].model_flags",
    "events[0].parts[0].text",
    "events[0].version",
    "events[1].author",
    "events[1].invocation_id",
    "events[1].parts[0].text",
    "events[2].author",
    "events[2].custom_metadata",
    "events[2].invocation_id",
    "events[2].is_summary",
    "events[2].model_flags",
    "events[2].parts[0].text",
    "events[2].version",
    "events[3]",
    "events[4]",
    "historical_events[0]",
    "historical_events[1]",
}

SUMMARY_GENERATION_UPDATE_ALLOWED_DIFF_REASON = (
    "After real summary compression, InMemory keeps the generated summary event before "
    "recent active events while SQLite reloads active and historical events in storage "
    "order. Summary cache content and strict summary metadata must still match."
)

SUMMARY_GENERATION_UPDATE_ALLOWED_DIFF_PATHS = {
    "events[0].author",
    "events[0].invocation_id",
    "events[0].is_summary",
    "events[0].model_flags",
    "events[0].partial",
    "events[0].parts[0].text",
    "events[1].author",
    "events[1].invocation_id",
    "events[1].parts[0].text",
    "events[2].author",
    "events[2].invocation_id",
    "events[2].is_summary",
    "events[2].model_flags",
    "events[2].partial",
    "events[2].parts[0].text",
    "historical_events[2].author",
    "historical_events[2].invocation_id",
    "historical_events[2].is_summary",
    "historical_events[2].model_flags",
    "historical_events[2].partial",
    "historical_events[2].parts[0].text",
    "historical_events[3].author",
    "historical_events[3].invocation_id",
    "historical_events[3].parts[0].text",
    "historical_events[4].author",
    "historical_events[4].invocation_id",
    "historical_events[4].is_summary",
    "historical_events[4].model_flags",
    "historical_events[4].partial",
    "historical_events[4].parts[0].text",
}

PERSISTENT_BACKEND_NAMES = ("sqlite_sql", "mock_redis", "env_sql", "env_redis")

ALLOWED_DIFFS = [
    {
        "case_name": "summary_generation_update",
        "backend_expected": "in_memory",
        "backend_actual": backend_name,
        "field_paths": SUMMARY_GENERATION_UPDATE_ALLOWED_DIFF_PATHS,
        "reason": SUMMARY_GENERATION_UPDATE_ALLOWED_DIFF_REASON,
    }
    for backend_name in PERSISTENT_BACKEND_NAMES
] + [
    {
        "case_name": "summary_truncation",
        "backend_expected": "in_memory",
        "backend_actual": backend_name,
        "field_paths": SUMMARY_TRUNCATION_ALLOWED_DIFF_PATHS,
        "reason": SUMMARY_TRUNCATION_ALLOWED_DIFF_REASON,
    }
    for backend_name in PERSISTENT_BACKEND_NAMES
]
