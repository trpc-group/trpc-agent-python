#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Unit tests for the replay harness normalizer."""

from __future__ import annotations

from tests.sessions.replay_harness._normalizer import (
    _sort_dict_deep,
    normalize_backend_result,
    normalize_events,
    normalize_memory_entries,
    normalize_state,
    normalize_summaries,
)
from tests.sessions.replay_harness._normalizer import NormalizedResult


def _make_event_dict(**overrides):
    """Build a realistic raw event dict as returned by Event.model_dump()."""
    event = {
        "id": "evt-aaa-111",
        "timestamp": 1752000123.456,
        "invocation_id": "inv-xyz",
        "author": "assistant",
        "branch": "agent.sub",
        "request_id": "req-999",
        "parent_invocation_id": "parent-inv-1",
        "tag": "some-tag",
        "filter_key": "a.b.c",
        "object": None,
        "partial": False,
        "visible": True,
        "version": 0,
        "requires_completion": False,
        "error_code": None,
        "error_message": None,
        "actions": {
            "state_delta": {},
            "artifact_delta": {},
            "skip_summarization": False,
            "transfer_to_agent": None,
            "escalate": None,
        },
        "content": {
            "parts": [{"text": "Hello world"}],
            "role": "model",
        },
        "model_flags": 1,
        "long_running_tool_ids": None,
    }
    event.update(overrides)
    return event


# ── _sort_dict_deep ────────────────────────────────────────────────────


class TestSortDictDeep:

    def test_sorts_top_level_keys(self):
        assert list(_sort_dict_deep({"b": 1, "a": 2})) == ["a", "b"]

    def test_recursively_sorts_nested_dicts(self):
        result = _sort_dict_deep({"z": {"b": 1, "a": 2}, "y": {"d": 4, "c": 3}})
        assert list(result) == ["y", "z"]
        assert list(result["z"]) == ["a", "b"]

    def test_handles_lists(self):
        result = _sort_dict_deep({"items": [{"b": 2, "a": 1}, {"d": 4, "c": 3}]})
        assert list(result["items"][0]) == ["a", "b"]
        assert list(result["items"][1]) == ["c", "d"]

    def test_passes_through_primitives(self):
        assert _sort_dict_deep(42) == 42
        assert _sort_dict_deep("hello") == "hello"
        assert _sort_dict_deep(None) is None


# ── normalize_events ───────────────────────────────────────────────────


class TestNormalizeEvents:

    def test_strips_auto_generated_fields(self):
        events = [_make_event_dict()]
        result = normalize_events(events)
        assert len(result) == 1
        norm = result[0]
        assert "id" not in norm
        assert "timestamp" not in norm
        assert "invocation_id" not in norm
        assert "branch" not in norm
        assert "request_id" not in norm

    def test_replaces_timestamp_with_sequential_index(self):
        events = [_make_event_dict(), _make_event_dict(), _make_event_dict()]
        result = normalize_events(events)
        assert [e["index"] for e in result] == [0, 1, 2]

    def test_empty_event_list_returns_empty(self):
        assert normalize_events([]) == []

    def test_preserves_author_and_text(self):
        events = [_make_event_dict(author="user", content={"parts": [{"text": "Hi"}]})]
        result = normalize_events(events)
        assert result[0]["author"] == "user"
        assert result[0]["text"] == "Hi"

    def test_extracts_function_calls(self):
        events = [
            _make_event_dict(content={
                "parts": [{
                    "function_call": {
                        "name": "search",
                        "args": {"query": "test"}
                    }
                }]
            })
        ]
        result = normalize_events(events)
        assert result[0]["function_calls"] == [
            {"name": "search", "args": {"query": "test"}}
        ]

    def test_extracts_function_responses(self):
        events = [
            _make_event_dict(content={
                "parts": [{
                    "function_response": {
                        "name": "get_temp",
                        "response": {"celsius": 22}
                    }
                }]
            })
        ]
        result = normalize_events(events)
        assert result[0]["function_responses"] == [
            {"name": "get_temp", "response": {"celsius": 22}}
        ]

    def test_preserves_state_delta(self):
        events = [
            _make_event_dict(actions={"state_delta": {"counter": 5}})
        ]
        result = normalize_events(events)
        assert result[0]["state_delta"] == {"counter": 5}

    def test_preserves_partial_and_visible_flags(self):
        events = [
            _make_event_dict(partial=True, visible=False)
        ]
        result = normalize_events(events)
        assert result[0]["partial"] is True
        assert result[0]["visible"] is False

    def test_concatentates_multiple_text_parts(self):
        events = [
            _make_event_dict(content={
                "parts": [{"text": "Hello "}, {"text": "world"}]
            })
        ]
        result = normalize_events(events)
        assert result[0]["text"] == "Hello world"


# ── normalize_state ────────────────────────────────────────────────────


class TestNormalizeState:

    def test_sorted_keys_deterministic(self):
        a = {"b": 1, "a": {"d": 2, "c": 3}}
        b = {"a": {"c": 3, "d": 2}, "b": 1}
        assert normalize_state(a) == normalize_state(b)

    def test_deeply_nested_normalized(self):
        state = {"level1": {"level2": {"z": 9, "a": 1}}}
        result = normalize_state(state)
        assert list(result["level1"]["level2"]) == ["a", "z"]

    def test_empty_state(self):
        assert normalize_state({}) == {}
        assert normalize_state({}) == {}


# ── normalize_summaries ────────────────────────────────────────────────


class TestNormalizeSummaries:

    def test_strips_timestamp_preserves_content(self):
        summaries = [
            {
                "session_id": "s-1",
                "summary_text": "A summary.",
                "original_event_count": 10,
                "compressed_event_count": 3,
                "summary_timestamp": 1752000999.0,
                "metadata": {"model": "gpt-4"},
            }
        ]
        result = normalize_summaries(summaries)
        assert result[0] == {
            "session_id": "s-1",
            "summary_text": "A summary.",
            "original_event_count": 10,
            "compressed_event_count": 3,
        }

    def test_preserves_event_counts(self):
        summaries = [
            {
                "session_id": "s-2",
                "summary_text": "x",
                "original_event_count": 42,
                "compressed_event_count": 7,
            }
        ]
        result = normalize_summaries(summaries)
        assert result[0]["original_event_count"] == 42
        assert result[0]["compressed_event_count"] == 7

    def test_preserves_session_id(self):
        summaries = [{
            "session_id": "abc-123",
            "summary_text": "",
            "original_event_count": 0,
            "compressed_event_count": 0,
        }]
        result = normalize_summaries(summaries)
        assert result[0]["session_id"] == "abc-123"

    def test_handles_empty_list(self):
        assert normalize_summaries([]) == []


# ── normalize_memory_entries ───────────────────────────────────────────


class TestNormalizeMemory:

    def test_strips_timestamp_preserves_content_and_author(self):
        entries = [
            {
                "content": {"parts": [{"text": "alice likes pizza"}], "role": "user"},
                "author": "user",
                "timestamp": "2025-01-01T00:00:00Z",
            }
        ]
        result = normalize_memory_entries(entries)
        assert result[0] == {
            "content_text": "alice likes pizza",
            "author": "user",
        }

    def test_empty_list(self):
        assert normalize_memory_entries([]) == []

    def test_multiple_entries_preserved(self):
        entries = [
            {"content": {"parts": [{"text": "a"}]}, "author": "u1"},
            {"content": {"parts": [{"text": "b"}]}, "author": "u2"},
        ]
        result = normalize_memory_entries(entries)
        assert len(result) == 2
        assert result[0]["content_text"] == "a"
        assert result[1]["content_text"] == "b"


# ── normalize_backend_result ───────────────────────────────────────────


class TestNormalizeBackendResult:

    def test_returns_normalized_result(self):
        events = [_make_event_dict(author="user", content={"parts": [{"text": "Q"}]})]
        result = normalize_backend_result(
            events=events,
            state={"k": "v"},
            summaries=[{
                "session_id": "s",
                "summary_text": "sum",
                "original_event_count": 1,
                "compressed_event_count": 1,
                "summary_timestamp": 999,
            }],
            memory_entries=[],
            errors=["err1"],
        )
        assert isinstance(result, NormalizedResult)
        assert len(result.events) == 1
        assert result.state == {"k": "v"}
        assert len(result.summaries) == 1
        assert result.errors == ["err1"]
