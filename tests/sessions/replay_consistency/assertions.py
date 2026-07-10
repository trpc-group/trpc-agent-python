# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Assertion functions for replay consistency tests."""

from __future__ import annotations

from typing import Any

from .constants import ALLOWED_DIFFS
from .constants import EXPECTED_REPLAY_CASE_FILES
from .constants import EXPECTED_REPLAY_CASE_NAMES
from .constants import REPLAY_CASES_DIR
from .loaders import REPLAY_CASES
from .models import ReplayCase
from .normalizers import all_normalized_events
from .normalizers import event_texts
from .normalizers import summary_events


def assert_replay_case_fixtures_load() -> None:
    """Assert that all replay case fixtures load correctly."""
    assert {replay_case.name for replay_case in REPLAY_CASES} == EXPECTED_REPLAY_CASE_NAMES
    assert [path.name for path in sorted(REPLAY_CASES_DIR.glob("*.jsonl"))] == EXPECTED_REPLAY_CASE_FILES


def assert_session_replay_case_snapshot(case_name: str, snapshot: dict[str, Any]) -> None:
    """Assert that a session snapshot matches expected values for a case."""
    if case_name == "single_turn":
        assert event_texts(snapshot) == ["hello", "hi"]
        assert snapshot["historical_events"] == []
    elif case_name == "multi_turn":
        assert event_texts(snapshot) == [
            "My name is Alice.",
            "Nice to meet you, Alice.",
            "What is the weather in Paris?",
            "Paris is sunny today.",
        ]
        assert snapshot["historical_events"] == []
    elif case_name == "tool_call":
        events = all_normalized_events(snapshot)
        assert any("function_call" in part for event in events for part in event["parts"])
        assert any("function_response" in part for event in events for part in event["parts"])
    elif case_name == "state_update":
        assert snapshot["state"]["profile.name"] == "Alice"
        assert snapshot["state"]["preference.color"] == "green"
    elif case_name == "memory_store_search":
        assert event_texts(snapshot) == [
            "Remember that Alice likes green tea.",
            "Noted.",
            "Alice prefers window seats when traveling.",
            "Saved.",
            "Alice's passport country is Canada.",
            "Saved.",
            "Summary: Alice is preparing a Shanghai travel profile from earlier preferences and facts.",
        ]
    elif case_name == "summary_generation_update":
        assert_summary_generation_update_snapshot(snapshot)
    elif case_name == "summary_truncation":
        assert event_texts({"historical_events": snapshot["historical_events"], "events": []}) == [
            "My name is Alice.",
            "Nice to meet you, Alice.",
        ]
        assert event_texts({"historical_events": [], "events": snapshot["events"]}) == [
            "Summary: Alice introduced herself.",
            "What is my name?",
            "Your name is Alice.",
        ]
        summary_evts = summary_events(snapshot)
        assert len(summary_evts) == 1
        assert summary_evts[0]["version"] == 1
        assert summary_evts[0]["custom_metadata"]["session_id"] == snapshot["session_id"]
        assert summary_evts[0]["custom_metadata"]["compressed_event_ids"] == ["event-1", "event-2"]
        assert event_texts(snapshot) == [
            "My name is Alice.",
            "Nice to meet you, Alice.",
            "Summary: Alice introduced herself.",
            "What is my name?",
            "Your name is Alice.",
        ]
    elif case_name == "recovery_partial_event":
        texts = event_texts(snapshot)
        assert "stream chunk should not persist" not in texts
        assert texts == ["Start a streaming answer.", "This is the completed answer."]
    elif case_name == "duplicate_event_replay":
        events = snapshot["events"]
        assert event_texts(snapshot) == [
            "Retry this request.",
            "Working on the retry.",
            "Retry this request.",
            "Working on the retry.",
            "Final answer after retry.",
        ]
        assert [event["invocation_id"] for event in events] == [
            "inv-1",
            "inv-2",
            "inv-1",
            "inv-2",
            "inv-3",
        ]
    elif case_name == "branch_metadata_replay":
        events = snapshot["events"]
        assert event_texts(snapshot) == [
            "Route this task to two branches.",
            "Design branch proposes the UI flow.",
            "Ops branch checks deployment constraints.",
            "Merged branch findings into one plan.",
        ]
        assert [event["branch"] for event in events] == [
            "main",
            "main.design",
            "main.ops",
            "main",
        ]
        assert events[0]["custom_metadata"] == {
            "attempt": 1,
            "labels": ["root", "routing"],
            "trace_id": "trace-branch-001",
        }
        assert events[1]["custom_metadata"]["score"] == {"quality": 0.91, "rank": 1}
        assert events[2]["custom_metadata"]["branch_role"] == "ops"
        assert events[3]["custom_metadata"]["decision"] == {
            "accepted": True,
            "owner": "agent",
        }
        assert events[3]["custom_metadata"]["merged_from"] == ["main.design", "main.ops"]
    else:
        raise AssertionError(f"Unexpected replay case: {case_name}")


def assert_all_diffs_allowed(report: list[dict[str, Any]]) -> None:
    """Assert that all diffs in a report are allowed."""
    unexpected_diffs = [entry for entry in report if not entry["allowed"]]
    assert unexpected_diffs == []


def uses_allowed_snapshot_variant(case_name: str, backend_name: str) -> bool:
    """Check if a case uses an allowed snapshot variant."""
    if case_name != "summary_truncation":
        return False
    return any(
        rule["case_name"] == case_name and rule["backend_expected"] == backend_name
        for rule in ALLOWED_DIFFS
    )


def assert_allowed_session_snapshot_variant(case_name: str, snapshot: dict[str, Any]) -> None:
    """Assert an allowed snapshot variant."""
    if case_name == "summary_truncation":
        assert_summary_truncation_in_memory_snapshot(snapshot)
        return
    raise AssertionError(f"Unexpected allowed divergence case: {case_name}")


def assert_summary_generation_update_snapshot(snapshot: dict[str, Any]) -> None:
    """Assert a real generated summary snapshot."""
    all_summary_evts = summary_events(snapshot)
    active_summary_evts = [event for event in snapshot["events"] if event["is_summary"]]
    assert len(all_summary_evts) == 2
    assert len(active_summary_evts) == 1
    assert snapshot["summary"] is not None
    summary_text = snapshot["summary"]["text"]
    summary_metadata = snapshot["summary"]["metadata"]

    assert summary_text.startswith("summary(session-summary-generation-update):")
    assert "Previous conversation summary:" in summary_text
    assert "Audience is SDK developers and operators." in summary_text
    assert "facts=3-events" in summary_text

    latest_summary_event = active_summary_evts[0]
    assert latest_summary_event["author"] == "system"
    assert latest_summary_event["invocation_id"] == "summary"
    assert latest_summary_event["parts"][0]["text"] == (
        "Previous conversation summary: " + summary_text
    )
    assert latest_summary_event["custom_metadata"] is None
    assert latest_summary_event["version"] == 0

    assert summary_metadata["session_id"] == snapshot["session_id"]
    assert summary_metadata["manager_session_id"] == snapshot["session_id"]
    assert summary_metadata["has_summary"] is True
    assert summary_metadata["has_summary_timestamp"] is True
    assert summary_metadata["summary_event_count"] == 1
    assert summary_metadata["summary_event_text"] == latest_summary_event["parts"][0]["text"]
    assert summary_metadata["compressed_event_count"] == 3
    assert summary_metadata["historical_event_count"] == 5
    assert summary_metadata["original_event_count"] == 5
    assert summary_metadata["manager_compressed_event_count"] == 3


def assert_summary_truncation_in_memory_snapshot(snapshot: dict[str, Any]) -> None:
    """Assert the summary truncation in-memory snapshot variant."""
    assert snapshot["historical_events"] == []
    assert event_texts(snapshot) == [
        "My name is Alice.",
        "Nice to meet you, Alice.",
        "Summary: Alice introduced herself.",
        "What is my name?",
        "Your name is Alice.",
    ]
    summary_evts = summary_events(snapshot)
    assert len(summary_evts) == 1
    assert summary_evts[0]["version"] == 1
    assert summary_evts[0]["custom_metadata"]["session_id"] == snapshot["session_id"]
    assert summary_evts[0]["custom_metadata"]["compressed_event_ids"] == ["event-1", "event-2"]


def assert_memory_replay_case_snapshot(replay_case: ReplayCase, snapshot: dict[str, Any]) -> None:
    """Assert that a memory snapshot matches expected values."""
    expected_searches = []
    for search_record in replay_case.memory_search_records:
        expected_memories = search_record.get("expected_memories")
        if expected_memories is None:
            expected_memories = [
                {"author": "user", "text": text}
                for text in search_record["expected_texts"]
            ]
        expected_searches.append({
            "query": search_record["query"],
            "limit": search_record.get("limit", 10),
            "memories": [
                {"author": memory["author"], "parts": [{"text": memory["text"]}]}
                for memory in expected_memories
            ],
        })
    assert snapshot["searches"] == expected_searches
