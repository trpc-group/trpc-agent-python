# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Unit tests for the replay consistency comparator."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest


@dataclasses.dataclass
class DiffEntry:
    """A single normalized field mismatch. Mirrors Go DiffEntry."""
    session_id: str | None = None
    event_index: int | None = None
    memory_id: str | None = None
    summary_id: str | None = None
    track_name: str | None = None
    section: str = ""
    path: str = ""
    left: Any = None
    right: Any = None
    allowed: bool = False
    reason: str = ""


class TestRecursiveDiff:
    """Tests for recursive_diff()."""

    def test_identical_dicts_no_diff(self):
        """Identical dicts produce zero diffs."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {"a": 1, "b": 2}
        right = {"a": 1, "b": 2}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 0

    def test_different_dict_values(self):
        """Different values produce diffs with correct paths."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {"a": 1, "b": 2}
        right = {"a": 1, "b": 99}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].path == "b"
        assert diffs[0].left == 2
        assert diffs[0].right == 99

    def test_missing_key_in_right(self):
        """Key present in left but missing in right."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {"a": 1, "b": 2}
        right = {"a": 1}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].path == "b"
        assert diffs[0].left == 2
        assert diffs[0].right is None

    def test_extra_key_in_right(self):
        """Key present in right but missing in left."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {"a": 1}
        right = {"a": 1, "b": 2}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].path == "b"
        assert diffs[0].left is None
        assert diffs[0].right == 2

    def test_identical_lists_no_diff(self):
        """Identical lists produce zero diffs."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = [1, 2, 3]
        right = [1, 2, 3]
        diffs = recursive_diff(left, right)
        assert len(diffs) == 0

    def test_list_length_mismatch(self):
        """Left longer than right — extra items diff as None."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = [1, 2, 3]
        right = [1, 2]
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].path == "[2]"
        assert diffs[0].left == 3
        assert diffs[0].right is None

    def test_list_value_mismatch(self):
        """Same length, different value."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = [1, 2, 3]
        right = [1, 99, 3]
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].path == "[1]"

    def test_nested_dict_in_list(self):
        """Nested dict inside list diff."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = [{"name": "Alice", "score": 10}]
        right = [{"name": "Alice", "score": 20}]
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert "score" in diffs[0].path

    def test_deeply_nested_structure(self):
        """Three-level nested diff."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {"events": [{"author": "user", "content": {"text": "Hi"}}]}
        right = {"events": [{"author": "user", "content": {"text": "Bye"}}]}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert "text" in diffs[0].path
        assert diffs[0].left == "Hi"
        assert diffs[0].right == "Bye"

    def test_primitive_string_diff(self):
        """Direct string comparison."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        diffs = recursive_diff("hello", "world")
        assert len(diffs) == 1

    def test_primitive_int_diff(self):
        """Direct int comparison."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        diffs = recursive_diff(42, 0)
        assert len(diffs) == 1

    def test_none_vs_value(self):
        """None vs actual value."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        diffs = recursive_diff(None, "data")
        assert len(diffs) == 1

    def test_detects_summary_injection(self):
        """Verify summary-specific diff detection."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {"summaries": {"": "Correct summary"}}
        right: dict[str, Any] = {"summaries": {}}
        diffs = recursive_diff(left, right)
        assert len(diffs) > 0, "Missing summary should be detected"

    def test_unicode_diff(self):
        """Unicode text diff should be detected correctly."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {"text": "你好世界"}
        right = {"text": "こんにちは"}
        diffs = recursive_diff(left, right)
        assert len(diffs) == 1
        assert diffs[0].left == "你好世界"

    def test_compound_session_snapshot(self):
        """Full session snapshot comparison with multiple diff types."""
        from tests.sessions.replay_consistency.comparator import recursive_diff
        left = {
            "events": [{"author": "user", "text": "Hello"}],
            "state": {"key": "value1"},
            "memories": [{"content": "test memory"}],
            "tracks": [{"track": "exec", "payload": '{"ok":true}'}],
        }
        right = {
            "events": [{"author": "user", "text": "Different"}],
            "state": {"key": "value2"},
            "memories": [{"content": "other memory"}],
            "tracks": [{"track": "exec", "payload": '{"ok":false}'}],
        }
        diffs = recursive_diff(left, right)
        # Should find diffs in all 4 sections.
        sections: set[str] = set()
        for d in diffs:
            path = d.path or ""
            top = path.split("[")[0].split(".")[0]
            sections.add(top)
        assert "events" in sections, f"Event diffs not detected in {sections}"
        assert "state" in sections, f"State diffs not detected in {sections}"
        assert "memories" in sections, f"Memory diffs not detected in {sections}"
        assert "tracks" in sections, f"Track diffs not detected in {sections}"
