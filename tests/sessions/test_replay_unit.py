# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for replay consistency normalizer, comparator, and allowed_diff."""

from __future__ import annotations

import pytest

from tests.sessions.replay_consistency.allowed_diff import AllowedDiffRule
from tests.sessions.replay_consistency.allowed_diff import MAX_ALLOWED_PER_CASE
from tests.sessions.replay_consistency.allowed_diff import MAX_ALLOWED_RATIO
from tests.sessions.replay_consistency.allowed_diff import check_governance
from tests.sessions.replay_consistency.allowed_diff import is_allowed
from tests.sessions.replay_consistency.comparator import compare_snapshot_pair
from tests.sessions.replay_consistency.comparator import recursive_diff
from tests.sessions.replay_consistency.comparator import unallowed_diffs
from tests.sessions.replay_consistency.harness import DiffEntry
from tests.sessions.replay_consistency.normalizer import NORMALIZED
from tests.sessions.replay_consistency.normalizer import normalize_snapshot
from tests.sessions.replay_consistency.summary_checks import SummaryComparator
from tests.sessions.replay_consistency.summary_checks import SummaryIssue
from tests.sessions.replay_consistency.summary_checks import SummaryIssueType
from tests.sessions.replay_consistency.summary_checks import summary_text_similarity


# ── Normalizer Tests ──────────────────────────────────────────────

class TestNormalizeSnapshot:
    """Tests for normalize_snapshot."""

    def test_replaces_timestamp(self):
        raw = {"timestamp": 1234567890.5, "events": [{"timestamp": 999.9, "text": "hello"}]}
        result = normalize_snapshot(raw)
        assert result["timestamp"] == NORMALIZED
        assert result["events"][0]["timestamp"] == NORMALIZED
        assert result["events"][0]["text"] == "hello"

    def test_replaces_auto_generated_ids(self):
        raw = {"id": "550e8400-e29b-41d4-a716-446655440000", "invocation_id": "abc-def-123"}
        result = normalize_snapshot(raw)
        assert result["id"] == NORMALIZED
        assert result["invocation_id"] == NORMALIZED

    def test_strips_temp_state_keys(self):
        raw = {"state": {"user:tier": "gold", "temp:trace_id": "xyz", "preference": "tea"}}
        result = normalize_snapshot(raw)
        assert "user:tier" in result["state"]
        assert "preference" in result["state"]
        assert "temp:trace_id" not in result["state"]

    def test_strips_temp_from_event_state_delta(self):
        raw = {
            "events": [{
                "state_delta": {"key": "val", "temp:debug": "noise"},
                "actions": {"state_delta": {"temp:x": "y", "real": "data"}},
            }]
        }
        result = normalize_snapshot(raw)
        event = result["events"][0]
        assert "temp:debug" not in event.get("state_delta", {})
        assert "temp:x" not in event.get("actions", {}).get("state_delta", {})

    def test_preserves_business_fields(self):
        raw = {"events": [{"author": "user", "text": "hello", "role": "user"}]}
        result = normalize_snapshot(raw)
        assert result["events"][0]["author"] == "user"
        assert result["events"][0]["text"] == "hello"

    def test_does_not_mutate_original(self):
        raw = {"state": {"temp:x": "y", "good": "keep"}}
        result = normalize_snapshot(raw)
        assert "temp:x" in raw["state"]
        assert "temp:x" not in result["state"]


# ── Comparator Tests ──────────────────────────────────────────────

class TestRecursiveDiff:
    """Tests for recursive_diff."""

    def test_identical_dicts_produce_no_diffs(self):
        left = {"a": 1, "b": {"c": 2}}
        right = {"a": 1, "b": {"c": 2}}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 0

    def test_different_values_produce_diff(self):
        left = {"a": 1}
        right = {"a": 2}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].path == "a"
        assert diffs[0].left == 1
        assert diffs[0].right == 2

    def test_missing_key_in_left(self):
        left = {}
        right = {"a": 1}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].left == "<missing>"
        assert diffs[0].right == 1

    def test_missing_key_in_right(self):
        left = {"a": 1}
        right = {}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].right == "<missing>"

    def test_list_length_mismatch(self):
        left = {"events": [{"text": "a"}]}
        right = {"events": [{"text": "a"}, {"text": "b"}]}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].path == "events[1]"

    def test_nested_path_generation(self):
        left = {"events": [{"content": {"parts": [{"text": "hello"}]}}]}
        right = {"events": [{"content": {"parts": [{"text": "world"}]}}]}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert "events" in diffs[0].path
        assert "text" in diffs[0].path

    def test_timestamp_diff_is_allowed(self):
        left = {"backend": "inmemory", "events": [{"timestamp": 1.0, "text": "hi"}]}
        right = {"backend": "sqlite", "events": [{"timestamp": 2.0, "text": "hi"}]}
        diffs = recursive_diff(left, right)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) == 0

    def test_compare_snapshot_pair(self):
        left = {"case_name": "test", "backend": "a", "events": []}
        right = {"case_name": "test", "backend": "b", "events": []}
        diffs = compare_snapshot_pair(left, right)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) == 0

    def test_backend_name_diff_allowed(self):
        left = {"backend": "inmemory"}
        right = {"backend": "sqlite"}
        diffs = recursive_diff(left, right)
        assert all(d.allowed for d in diffs)

    def test_normalized_value_diff_allowed(self):
        left = {"events": [{"timestamp": NORMALIZED, "text": "hi"}]}
        right = {"events": [{"timestamp": NORMALIZED, "text": "hi"}]}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 0


# ── AllowedDiff Tests ─────────────────────────────────────────────

class TestAllowedDiffRule:
    """Tests for AllowedDiffRule pattern matching."""

    def test_exact_path_match(self):
        rule = AllowedDiffRule(path="events[0].timestamp", reason="test")
        assert rule.matches("events[0].timestamp")
        assert not rule.matches("events[0].text")
        assert not rule.matches("events[1].timestamp")

    def test_wildcard_index_match(self):
        rule = AllowedDiffRule(path="events[*].timestamp", reason="test")
        assert rule.matches("events[0].timestamp")
        assert rule.matches("events[5].timestamp")
        assert rule.matches("events[10].timestamp")
        assert not rule.matches("events[0].text")
        assert not rule.matches("historical_events[0].timestamp")

    def test_is_allowed_function(self):
        rules = (
            AllowedDiffRule(path="events[*].timestamp", reason="test"),
            AllowedDiffRule(path="backend", reason="test"),
        )
        ok, reason = is_allowed("events[3].timestamp", rules)
        assert ok is True
        assert reason == "test"

    def test_not_allowed_without_rule(self):
        ok, reason = is_allowed("events[0].text", ())
        assert ok is False
        assert reason == ""


class TestAllowedDiffGovernance:
    """Tests for allowed diff governance limits."""

    def test_within_limit_passes(self):
        check_governance(total_fields=100, used_allowed=5)

    def test_exceeds_count_limit(self):
        with pytest.raises(AssertionError, match="per-case limit"):
            check_governance(total_fields=100, used_allowed=MAX_ALLOWED_PER_CASE + 1)

    def test_exceeds_ratio_limit(self):
        with pytest.raises(AssertionError, match="per-case limit"):
            check_governance(total_fields=20, used_allowed=3)

    def test_zero_fields_no_division_error(self):
        check_governance(total_fields=0, used_allowed=0)


# ── Summary Checks Tests ──────────────────────────────────────────

class TestJaccardSimilarity:
    """Tests for summary_text_similarity."""

    def test_identical_texts(self):
        assert summary_text_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        sim = summary_text_similarity("hello world", "goodbye planet")
        assert sim < 0.5

    def test_empty_strings(self):
        assert summary_text_similarity("", "") == 1.0

    def test_one_empty(self):
        assert summary_text_similarity("hello", "") == 0.0

    def test_similar_chinese_texts(self):
        a = "今天天气很好适合出去玩"
        b = "今天天气不错适合外出游玩"
        sim = summary_text_similarity(a, b)
        assert 0.3 < sim < 1.0


class TestSummaryComparator:
    """Tests for SummaryComparator."""

    def test_both_none_no_issues(self):
        comp = SummaryComparator()
        diffs, issues = comp.compare(None, None, "session-1")
        assert len(diffs) == 0
        assert len(issues) == 0

    def test_left_loss_detected(self):
        comp = SummaryComparator()
        diffs, issues = comp.compare(None, {"summary_text": "present"}, "session-1",
                                     left_backend="inmemory", right_backend="sqlite")
        assert len(issues) == 1
        assert issues[0].type == SummaryIssueType.LOSS

    def test_right_loss_detected(self):
        comp = SummaryComparator()
        diffs, issues = comp.compare({"summary_text": "present"}, None, "session-1")
        assert len(issues) == 1
        assert issues[0].type == SummaryIssueType.LOSS

    def test_text_similarity_below_threshold(self):
        comp = SummaryComparator(similarity_threshold=0.9)
        diffs, issues = comp.compare(
            {"summary_text": "hello world", "metadata": {}},
            {"summary_text": "completely different summary text goes here"},
            "session-1",
        )
        assert len(diffs) >= 1

    def test_affiliation_mismatch(self):
        comp = SummaryComparator()
        diffs, issues = comp.compare(
            {"summary_text": "test", "metadata": {"session_id": "session-2"}, "session_id": "session-2"},
            {"summary_text": "test", "metadata": {"session_id": "session-1"}, "session_id": "session-1"},
            "session-1",
            left_backend="inmemory", right_backend="sqlite",
        )
        assert len(issues) >= 1
        assert any(i.type == SummaryIssueType.AFFILIATION for i in issues)
