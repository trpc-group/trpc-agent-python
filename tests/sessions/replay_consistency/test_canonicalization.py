# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Canonicalization guardrails against over-normalization."""

from __future__ import annotations

import copy

from .canonicalize import canonicalize_snapshot
from .diff import compare_snapshots


def _snapshot():
    return {
        "backend": "test",
        "sessions": [
            {
                "app_name": "app",
                "user_id": "user",
                "session_id": "s1",
                "state": {},
                "events": [
                    {
                        "index": 0,
                        "event_id": "tool_call",
                        "actual_event_id": "actual-1",
                        "timestamp": 1.0,
                        "content": {
                            "parts": [
                                {
                                    "function_call": {
                                        "id": "call-1",
                                        "name": "tool",
                                        "args": {"b": 2, "a": 1},
                                    }
                                }
                            ]
                        },
                        "actions": {},
                        "function_calls": [{"id": "call-1", "name": "tool", "args": {"b": 2, "a": 1}}],
                        "function_responses": [],
                    },
                    {
                        "index": 1,
                        "event_id": "tool_response",
                        "actual_event_id": "actual-2",
                        "timestamp": 2.0,
                        "content": {
                            "parts": [
                                {
                                    "function_response": {
                                        "id": "call-1",
                                        "name": "tool",
                                        "response": {"ok": True},
                                    }
                                }
                            ]
                        },
                        "actions": {},
                        "function_calls": [],
                        "function_responses": [{"id": "call-1", "name": "tool", "response": {"ok": True}}],
                    },
                ],
                "historical_events": [],
                "conversation_count": 2,
            }
        ],
        "memory": [],
        "summaries": [],
        "backend_metadata": {},
    }


def test_canonicalization_does_not_hide_event_reordering():
    reference = canonicalize_snapshot(_snapshot())
    reordered_source = _snapshot()
    reordered_source["sessions"][0]["events"].reverse()
    actual = canonicalize_snapshot(reordered_source)

    categories = {
        diff.category
        for diff in compare_snapshots(reference, actual, case_id="order", backend_pair=("a", "b"))
    }

    assert "event_order_mismatch" in categories


def test_canonicalization_preserves_tool_call_linkage():
    reference = canonicalize_snapshot(_snapshot())
    broken_source = copy.deepcopy(reference)
    broken_source["sessions"][0]["events"][1]["function_responses"][0]["id"] = "wrong-call"

    categories = {
        diff.category
        for diff in compare_snapshots(reference, broken_source, case_id="tool", backend_pair=("a", "b"))
    }

    assert "tool_link_mismatch" in categories


def test_timestamp_normalization_preserves_relative_order_signal():
    reference = canonicalize_snapshot(_snapshot())
    broken_source = _snapshot()
    broken_source["sessions"][0]["events"][0]["timestamp"] = 3.0
    broken_source["sessions"][0]["events"][1]["timestamp"] = 2.0
    actual = canonicalize_snapshot(broken_source)

    diffs = compare_snapshots(reference, actual, case_id="time", backend_pair=("a", "b"))

    assert reference["sessions"][0]["event_timestamps_monotonic"]
    assert not actual["sessions"][0]["event_timestamps_monotonic"]
    assert any(diff.field_path.endswith("event_timestamps_monotonic") for diff in diffs)
