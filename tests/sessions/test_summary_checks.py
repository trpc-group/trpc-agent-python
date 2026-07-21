# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Summary fault detection tests — loss, overwrite, affiliation.

Verifies the three mandatory summary fault categories are detected
at 100% detection rate (acceptance criterion #4 from Issue #89).
"""

from __future__ import annotations

import pytest

from tests.sessions.replay_consistency.summary_checks import SummaryComparator
from tests.sessions.replay_consistency.summary_checks import SummaryIssueType
from tests.sessions.replay_consistency.summary_checks import summary_text_similarity


class TestSummaryFaultDetection:
    """Tests for the three mandatory summary fault categories."""

    def test_detect_summary_loss_left_missing(self):
        """summary loss: reference has summary, candidate has None."""
        comp = SummaryComparator()
        diffs, issues = comp.compare(
            {"summary_text": "The user asked about weather.", "metadata": {}},
            None,
            "session-1",
            left_backend="inmemory",
            right_backend="sqlite",
        )
        assert any(i.type == SummaryIssueType.LOSS for i in issues), \
            f"Expected LOSS issue, got {issues}"

    def test_detect_summary_loss_right_missing(self):
        """summary loss: reference has None, candidate has summary."""
        comp = SummaryComparator()
        diffs, issues = comp.compare(
            None,
            {"summary_text": "The user asked about weather.", "metadata": {}},
            "session-1",
        )
        assert any(i.type == SummaryIssueType.LOSS for i in issues)

    def test_detect_summary_overwrite_version_regression(self):
        """overwrite: version regresses from 2 → 1."""
        comp = SummaryComparator()
        diffs, issues = comp.compare(
            {"summary_text": "User prefers tea.", "metadata": {}, "version": "2"},
            {"summary_text": "User prefers tea.", "metadata": {}, "version": "1"},
            "session-1",
            left_backend="inmemory",
            right_backend="sqlite",
        )
        assert any(i.type == SummaryIssueType.OVERWRITE for i in issues), \
            f"Expected OVERWRITE issue, got {issues}"

    def test_detect_summary_affiliation_wrong_session(self):
        """affiliation: summary's metadata.session_id != owning session."""
        comp = SummaryComparator()
        diffs, issues = comp.compare(
            {"summary_text": "test", "metadata": {"session_id": "session-other"}, "session_id": "session-other"},
            {"summary_text": "test", "metadata": {"session_id": "session-owned"}, "session_id": "session-owned"},
            "session-owned",
            left_backend="inmemory",
            right_backend="sqlite",
        )
        assert any(i.type == SummaryIssueType.AFFILIATION for i in issues), \
            f"Expected AFFILIATION issue, got {issues}"

    def test_all_three_categories_have_unique_types(self):
        """Loss, overwrite, and affiliation are distinct types."""
        assert SummaryIssueType.LOSS != SummaryIssueType.OVERWRITE
        assert SummaryIssueType.OVERWRITE != SummaryIssueType.AFFILIATION
        assert SummaryIssueType.AFFILIATION != SummaryIssueType.LOSS

    def test_no_false_positive_on_identical_summaries(self):
        """Identical summaries produce zero issues."""
        comp = SummaryComparator()
        summary = {
            "summary_text": "The conversation covered Python web development topics.",
            "metadata": {"session_id": "session-1"},
            "version": "1",
            "original_event_count": 5,
        }
        diffs, issues = comp.compare(
            summary, summary,
            "session-1",
            left_backend="inmemory",
            right_backend="sqlite",
        )
        assert len(issues) == 0, f"Expected 0 issues on identical summaries, got {issues}"
        assert len(diffs) == 0, f"Expected 0 diffs on identical summaries, got {diffs}"

    def test_similar_summaries_within_threshold(self):
        """Summaries with Jaccard > 0.80 should not trigger text diff."""
        a = "The user discussed Python web development with FastAPI and database options."
        b = "The user discussed Python web development with FastAPI and considered database options."
        sim = summary_text_similarity(a, b)
        assert sim >= 0.80, f"Expected Jaccard >= 0.80, got {sim:.2f}"

    def test_dissimilar_summaries_below_threshold(self):
        """Summaries with Jaccard < 0.80 should trigger text diff."""
        a = "This is about weather forecasting in Beijing."
        b = "This is about database migration strategies for PostgreSQL."
        sim = summary_text_similarity(a, b)
        assert sim < 0.80, f"Expected Jaccard < 0.80, got {sim:.2f}"
