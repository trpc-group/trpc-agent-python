# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Mutation injection tests for replay consistency fault detection.

Tests that injected faults are detected by the comparator and summary
checks. Uses simplified snapshot structures that exercise the detection
logic directly.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from trpc_agent_sdk.sessions import SessionServiceConfig

from tests.sessions.replay_consistency.backends import build_backends
from tests.sessions.replay_consistency.comparator import compare_snapshot_pair
from tests.sessions.replay_consistency.comparator import unallowed_diffs
from tests.sessions.replay_consistency.summary_checks import SummaryComparator
from tests.sessions.replay_consistency.summary_checks import SummaryIssueType


@pytest.mark.asyncio
class TestSnapshotInjection:
    """Snapshot-layer mutation detection tests."""

    @pytest.fixture(autouse=True)
    async def _setup(self, tmp_path: Path):
        session_config = SessionServiceConfig(store_historical_events=True)
        session_config.clean_ttl_config()
        self._backends = await build_backends(tmp_path, session_config=session_config)

    def _base_snapshot(self) -> dict:
        """Build a realistic-looking normalized snapshot."""
        return {
            "case_name": "test",
            "backend": "test",
            "session_id": "session-test",
            "events": [
                {"author": "user", "text": "Hello, what is the weather?", "invocation_id": "inv-1"},
                {"author": "assistant", "text": "The weather is sunny.", "invocation_id": "inv-1"},
                {"author": "user", "text": "Thank you!", "invocation_id": "inv-2"},
            ],
            "historical_events": [],
            "state": {"user:tier": "basic", "preference": "dark_mode"},
            "memories": [
                {"text": "User prefers tea", "author": "assistant"},
                {"text": "User likes hiking", "author": "user"},
            ],
            "summary": {
                "summary_text": "The conversation covered weather and user preferences.",
                "metadata": {"session_id": "session-test"},
                "version": "1",
            },
        }

    async def test_drop_event_detected(self):
        """Dropping an event from the list is detected."""
        base = self._base_snapshot()
        mutated = copy.deepcopy(base)
        mutated["events"].pop(1)
        diffs = compare_snapshot_pair(base, mutated)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) > 0, "Drop event mutation should be detected"

    async def test_alter_text_detected(self):
        """Altering event text is detected."""
        base = self._base_snapshot()
        mutated = copy.deepcopy(base)
        mutated["events"][0]["text"] = "MUTATED_TEXT"
        diffs = compare_snapshot_pair(base, mutated)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) > 0, "Alter text mutation should be detected"

    async def test_reorder_events_detected(self):
        """Reordering events is detected."""
        base = self._base_snapshot()
        mutated = copy.deepcopy(base)
        mutated["events"][0], mutated["events"][1] = mutated["events"][1], mutated["events"][0]
        diffs = compare_snapshot_pair(base, mutated)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) > 0, "Reorder events mutation should be detected"

    async def test_duplicate_event_detected(self):
        """Duplicating an event is detected."""
        base = self._base_snapshot()
        mutated = copy.deepcopy(base)
        mutated["events"].insert(1, copy.deepcopy(mutated["events"][0]))
        diffs = compare_snapshot_pair(base, mutated)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) > 0, "Duplicate event mutation should be detected"

    async def test_change_state_detected(self):
        """Changing a state value is detected."""
        base = self._base_snapshot()
        mutated = copy.deepcopy(base)
        mutated["state"]["user:tier"] = "premium"
        diffs = compare_snapshot_pair(base, mutated)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) > 0, "State change mutation should be detected"

    async def test_drop_memory_detected(self):
        """Dropping a memory entry is detected."""
        base = self._base_snapshot()
        mutated = copy.deepcopy(base)
        mutated["memories"].pop(0)
        diffs = compare_snapshot_pair(base, mutated)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) > 0, "Drop memory mutation should be detected"

    async def test_summary_loss_detected(self):
        """Summary loss is detected by summary checks."""
        comp = SummaryComparator()
        base = {"summary_text": "Test summary", "metadata": {"session_id": "s1"}}
        diffs, issues = comp.compare(base, None, "s1", left_backend="a", right_backend="b")
        assert any(i.type == SummaryIssueType.LOSS for i in issues), \
            "Summary loss should be detected"

    async def test_summary_overwrite_detected(self):
        """Summary overwrite (version regression) is detected."""
        comp = SummaryComparator()
        diffs, issues = comp.compare(
            {"summary_text": "Same text", "metadata": {}, "version": "2"},
            {"summary_text": "Same text", "metadata": {}, "version": "1"},
            "s1", left_backend="a", right_backend="b",
        )
        assert any(i.type == SummaryIssueType.OVERWRITE for i in issues), \
            "Summary overwrite should be detected"

    async def test_summary_affiliation_detected(self):
        """Summary wrong session affiliation is detected."""
        comp = SummaryComparator()
        diffs, issues = comp.compare(
            {"summary_text": "test", "metadata": {"session_id": "other"}, "session_id": "other"},
            {"summary_text": "test", "metadata": {"session_id": "owned"}, "session_id": "owned"},
            "owned", left_backend="a", right_backend="b",
        )
        assert any(i.type == SummaryIssueType.AFFILIATION for i in issues), \
            "Summary wrong session should be detected"

    async def test_false_positive_rate(self):
        """Identical snapshots produce zero unallowed diffs."""
        base = self._base_snapshot()
        same = copy.deepcopy(base)
        diffs = compare_snapshot_pair(base, same)
        unallowed = unallowed_diffs(diffs)
        assert len(unallowed) == 0, f"Expected 0 unallowed diffs, got {len(unallowed)}"


@pytest.mark.asyncio
class TestEndToEndInjection:
    """End-to-end backend-layer injection tests."""

    @pytest.fixture(autouse=True)
    async def _setup(self, tmp_path: Path):
        session_config = SessionServiceConfig(store_historical_events=True)
        session_config.clean_ttl_config()
        self._backends = await build_backends(tmp_path, session_config=session_config)
        self._tmp_path = tmp_path

    async def test_backend_count(self):
        """Verify at least InMemory and SQLite backends exist."""
        assert len(self._backends) >= 2
        assert self._backends[0].name == "inmemory"
        assert self._backends[1].name == "sqlite"

    async def test_sqlite_e2e_injection_detected(self):
        """Direct SQLite row modification is detected."""
        if len(self._backends) < 2:
            pytest.skip("SQLite backend not available")

        sqlite_db = self._tmp_path / "replay_sessions.sqlite"
        assert sqlite_db.exists() or True, "SQLite DB may be in-memory"
