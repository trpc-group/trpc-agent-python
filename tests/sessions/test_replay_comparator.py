#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Unit tests for the replay harness comparator."""

from __future__ import annotations

from tests.sessions.replay_harness._comparator import (
    AllowedDiff,
    AllowedDiffRule,
    DiffEntry,
    _diff_events,
    _diff_memory,
    _diff_state,
    _diff_summaries,
    compare_results,
)
from tests.sessions.replay_harness._normalizer import NormalizedResult


BP = ("in_memory", "sql")

# ── helpers ────────────────────────────────────────────────────────────


def _norm(events=None, state=None, summaries=None, memory_entries=None):
    return NormalizedResult(
        events=events or [],
        state=state or {},
        summaries=summaries or [],
        memory_entries=memory_entries or [],
        errors=[],
    )


def _make_ev(**kw):
    defaults = {
        "index": 0,
        "author": "user",
        "text": "",
        "function_calls": [],
        "function_responses": [],
        "state_delta": {},
        "partial": False,
        "visible": True,
        "error_code": None,
        "error_message": None,
    }
    defaults.update(kw)
    return defaults


def _make_sum(**kw):
    defaults = {
        "session_id": "s-1",
        "summary_text": "A summary.",
        "original_event_count": 10,
        "compressed_event_count": 3,
    }
    defaults.update(kw)
    return defaults


# ── _diff_events ───────────────────────────────────────────────────────


class TestDiffEvents:

    def test_identical_no_diffs(self):
        e = [_make_ev(text="hello")]
        assert _diff_events(e, e) == []

    def test_different_count_detected(self):
        assert len(_diff_events([_make_ev()], [])) > 0

    def test_different_author_detected(self):
        diffs = _diff_events([_make_ev(author="user")], [_make_ev(author="bot")])
        assert any(d.field_path.endswith(".author") for d in diffs)

    def test_different_text_detected(self):
        diffs = _diff_events([_make_ev(text="a")], [_make_ev(text="b")])
        assert any(d.field_path.endswith(".text") for d in diffs)

    def test_different_function_call_detected(self):
        a = [_make_ev(function_calls=[{"name": "f1", "args": {}}])]
        b = [_make_ev(function_calls=[{"name": "f2", "args": {}}])]
        assert _diff_events(a, b)

    def test_different_function_response_detected(self):
        a = [_make_ev(function_responses=[{"name": "r1", "response": {}}])]
        b = [_make_ev(function_responses=[{"name": "r2", "response": {}}])]
        assert _diff_events(a, b)

    def test_different_state_delta_detected(self):
        a = [_make_ev(state_delta={"x": 1})]
        b = [_make_ev(state_delta={"x": 2})]
        diffs = _diff_events(a, b)
        assert any("state_delta" in d.field_path for d in diffs)

    def test_field_path_precision(self):
        a = [
            _make_ev(index=0, text="ok"),
            _make_ev(index=1, author="user", text="broken"),
        ]
        b = [
            _make_ev(index=0, text="ok"),
            _make_ev(index=1, author="admin", text="broken"),
        ]
        diffs = _diff_events(a, b)
        author_diffs = [d for d in diffs if d.field_path.endswith(".author")]
        assert len(author_diffs) == 1
        assert author_diffs[0].event_index == 1
        assert author_diffs[0].field_path == "events[1].author"

    def test_extra_event_in_a_detected(self):
        diffs = _diff_events([_make_ev(), _make_ev()], [_make_ev()])
        assert any(d.field_path == "events.length" for d in diffs)

    def test_extra_event_in_b_detected(self):
        diffs = _diff_events([_make_ev()], [_make_ev(), _make_ev()])
        assert any(d.field_path == "events.length" for d in diffs)


# ── _diff_state ────────────────────────────────────────────────────────


class TestDiffState:

    def test_identical_no_diffs(self):
        assert _diff_state({"a": 1}, {"a": 1}) == []

    def test_value_diff_with_field_path(self):
        diffs = _diff_state({"counter": 1}, {"counter": 2})
        assert len(diffs) == 1
        assert diffs[0].field_path == "state.counter"
        assert diffs[0].value_a == 1
        assert diffs[0].value_b == 2

    def test_key_missing_detected(self):
        diffs = _diff_state({"a": 1}, {})
        assert len(diffs) == 1
        assert diffs[0].value_b == "<missing>"

    def test_nested_diff(self):
        diffs = _diff_state({"nested": {"x": 10}}, {"nested": {"x": 20}})
        assert len(diffs) == 1
        assert diffs[0].field_path == "state.nested.x"


# ── _diff_summaries ────────────────────────────────────────────────────


class TestDiffSummary:

    def test_identical_no_diffs(self):
        assert _diff_summaries([_make_sum()], [_make_sum()]) == []

    def test_summary_text_diff(self):
        diffs = _diff_summaries([_make_sum(summary_text="A")], [_make_sum(summary_text="B")])
        assert any("summary_text" in d.field_path for d in diffs)

    def test_session_id_diff_overwrite_detection(self):
        diffs = _diff_summaries(
            [_make_sum(session_id="s-1", summary_text="Original")],
            [_make_sum(session_id="s-1", summary_text="Corrupted")],
        )
        assert any("summary_text" in d.field_path for d in diffs)

    def test_summary_loss_present_in_a_absent_in_b(self):
        diffs = _diff_summaries([_make_sum(session_id="s-1")], [])
        assert len(diffs) == 1
        assert diffs[0].value_a == "<present>"
        assert diffs[0].value_b == "<missing>"

    def test_summary_loss_present_in_b_absent_in_a(self):
        diffs = _diff_summaries([], [_make_sum(session_id="s-1")])
        assert len(diffs) == 1
        assert diffs[0].value_a == "<missing>"
        assert diffs[0].value_b == "<present>"

    def test_event_count_diff_detected(self):
        diffs = _diff_summaries(
            [_make_sum(original_event_count=5)],
            [_make_sum(original_event_count=10)],
        )
        assert any("original_event_count" in d.field_path for d in diffs)

    def test_wrong_session_affiliation_detected(self):
        diffs = _diff_summaries(
            [_make_sum(session_id="s-1", summary_text="The data")],
            [_make_sum(session_id="s-2", summary_text="The data")],
        )
        loss = any(
            d.value_a == "<present>" and d.value_b == "<missing>"
            and d.summary_id == "s-1" for d in diffs
        )
        extra = any(
            d.value_a == "<missing>" and d.value_b == "<present>"
            and d.summary_id == "s-2" for d in diffs
        )
        assert loss and extra


# ── _diff_memory ───────────────────────────────────────────────────────


class TestDiffMemory:

    def test_identical_no_diffs(self):
        a = [{"content_text": "hello", "author": "user"}]
        assert _diff_memory(a, a) == []

    def test_different_content_text(self):
        a = [{"content_text": "hello", "author": "user"}]
        b = [{"content_text": "world", "author": "user"}]
        diffs = _diff_memory(a, b)
        assert any("content_text" in d.field_path for d in diffs)

    def test_different_author(self):
        a = [{"content_text": "x", "author": "user"}]
        b = [{"content_text": "x", "author": "bot"}]
        diffs = _diff_memory(a, b)
        assert any("author" in d.field_path for d in diffs)

    def test_entry_count_diff(self):
        diffs = _diff_memory([{"content_text": "a", "author": "u"}], [])
        assert any("memory.length" in d.field_path for d in diffs)


# ── AllowedDiff ────────────────────────────────────────────────────────


class TestAllowedDiff:

    def test_allowed_rule_suppresses_match(self):
        allowed = AllowedDiff(rules=[
            AllowedDiffRule(
                field_path_pattern="events[*].text",
                reason="test",
                backend_pairs=[BP],
            )
        ])
        a = _norm(events=[_make_ev(text="a")])
        b = _norm(events=[_make_ev(text="b")])
        diffs = compare_results(a, b, backend_pair=BP, allowed=allowed)
        text_diffs = [d for d in diffs if "text" in d.field_path]
        assert len(text_diffs) == 1
        assert text_diffs[0].allowed is True

    def test_allowed_rule_does_not_suppress_non_match(self):
        allowed = AllowedDiff(rules=[
            AllowedDiffRule(
                field_path_pattern="events[*].function_calls[*].args",
                reason="test",
                backend_pairs=[BP],
            )
        ])
        a = _norm(events=[_make_ev(text="a")])
        b = _norm(events=[_make_ev(text="b")])
        diffs = compare_results(a, b, backend_pair=BP, allowed=allowed)
        text_diffs = [d for d in diffs if "text" in d.field_path]
        assert len(text_diffs) == 1
        assert text_diffs[0].allowed is False

    def test_allowed_rule_respects_backend_pair(self):
        allowed = AllowedDiff(rules=[
            AllowedDiffRule(
                field_path_pattern="events[*].text",
                reason="test",
                backend_pairs=[("in_memory", "redis")],
            )
        ])
        a = _norm(events=[_make_ev(text="a")])
        b = _norm(events=[_make_ev(text="b")])
        diffs = compare_results(a, b, backend_pair=BP, allowed=allowed)
        text_diffs = [d for d in diffs if "text" in d.field_path]
        assert len(text_diffs) == 1
        assert text_diffs[0].allowed is False


# ── compare_results (integration) ──────────────────────────────────────


class TestCompareResults:

    def test_identical_no_diffs(self):
        a = _norm(
            events=[_make_ev(text="hello")],
            state={"k": "v"},
            summaries=[_make_sum()],
            memory_entries=[{"content_text": "x", "author": "u"}],
        )
        assert compare_results(a, a) == []

    def test_diff_entry_has_all_fields(self):
        a = _norm(events=[_make_ev(text="a")])
        b = _norm(events=[_make_ev(text="b")])
        diffs = compare_results(a, b, backend_pair=BP)
        assert len(diffs) >= 1
        d = diffs[0]
        assert isinstance(d, DiffEntry)
        assert d.backend_pair == BP
        assert d.category in ("events", "state", "memory", "summary")
        assert d.field_path
