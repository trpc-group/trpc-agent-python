# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay harness 单元测试:模型 / normalizer / comparator / allowed_diff / summary_checks / report。"""

from __future__ import annotations

import pytest

from tests.sessions.replay.allowed_diff import MAX_ALLOWED_PER_CASE
from tests.sessions.replay.allowed_diff import check_governance
from tests.sessions.replay.allowed_diff import is_allowed
from tests.sessions.replay.comparator import MISSING
from tests.sessions.replay.comparator import DiffEntry
from tests.sessions.replay.comparator import compare_snapshots
from tests.sessions.replay.harness import AllowedDiffRule
from tests.sessions.replay.harness import ReplayCase
from tests.sessions.replay.harness import ReplayOp
from tests.sessions.replay.harness import ReplaySnapshot
from tests.sessions.replay.normalizer import NORMALIZED
from tests.sessions.replay.normalizer import normalize_event
from tests.sessions.replay.normalizer import normalize_snapshot
from tests.sessions.replay.report import BackendStatus
from tests.sessions.replay.report import CaseResult
from tests.sessions.replay.report import Comparison
from tests.sessions.replay.report import build_diff_report
from tests.sessions.replay.summary_checks import check_summary_issues
from tests.sessions.replay.summary_checks import summary_text_similarity

# ---------------------------------------------------------------------------
# Task 2: 数据模型
# ---------------------------------------------------------------------------


class TestReplayModels:

    def test_replay_case_roundtrip(self):
        case = ReplayCase(
            case_id="single_turn",
            description="one turn",
            operations=[
                ReplayOp(op="create_session", app_name="a", user_id="u", session_id="s"),
                ReplayOp(op="append_event", author="user", text="hi"),
            ],
            allowed_diff=[AllowedDiffRule(path="events[*].timestamp", reason="auto")],
        )
        back = ReplayCase.model_validate_json(case.model_dump_json())
        assert back.case_id == "single_turn"
        assert back.operations[0].op == "create_session"
        assert back.operations[1].text == "hi"
        assert back.allowed_diff[0].reason == "auto"

    def test_replay_op_rejects_unknown_field(self):
        with pytest.raises(Exception):
            ReplayOp(op="append_event", not_a_field=1)


def _snapshot(**kw) -> ReplaySnapshot:
    return ReplaySnapshot(session_id="s1", **kw)


def _diff(**kw) -> DiffEntry:
    return DiffEntry(
        field_path="events[0].author",
        reference_backend="in_memory",
        candidate_backend="sqlite",
        reference_value="user",
        candidate_value="assistant",
        **kw,
    )


# ---------------------------------------------------------------------------
# Task 3: normalizer
# ---------------------------------------------------------------------------


class TestNormalizer:

    def test_normalize_event_replaces_volatile_fields(self):
        e = normalize_event({"id": "u1", "timestamp": 1.2, "invocation_id": "i", "author": "user"})
        assert e["id"] == NORMALIZED
        assert e["timestamp"] == NORMALIZED
        assert e["invocation_id"] == NORMALIZED
        assert e["author"] == "user"

    def test_normalize_strips_temp_state(self):
        snap = normalize_snapshot(_snapshot(state={"app:x": 1, "temp:skip": 2, "plain": 3}))
        assert "temp:skip" not in snap.state
        assert snap.state["app:x"] == 1
        assert snap.state["plain"] == 3

    def test_normalize_sorts_memory(self):
        snap = normalize_snapshot(_snapshot(memory={"q": [{"b": 2}, {"a": 1}, {"c": 3}]}))
        keys = [list(m.keys())[0] for m in snap.memory["q"]]
        assert keys == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Task 4: comparator
# ---------------------------------------------------------------------------


class TestComparator:

    def test_diff_detects_leaf_mismatch(self):
        ref = _snapshot(events=[{"author": "user", "content": {"parts": [{"text": "hi"}]}}])
        cand = _snapshot(events=[{"author": "assistant", "content": {"parts": [{"text": "hi"}]}}])
        diffs = compare_snapshots(ref, cand, reference_backend="in_memory", candidate_backend="sqlite", allowed_diff=[])
        assert len(diffs) == 1
        d = diffs[0]
        assert d.field_path == "events[0].author"
        assert d.event_index == 0
        assert d.reference_value == "user"
        assert d.candidate_value == "assistant"
        assert d.allowed is False

    def test_diff_aligns_dict_sorted_keys(self):
        ref = _snapshot(state={"b": 1, "a": 2})
        cand = _snapshot(state={"a": 2, "b": 1})
        assert compare_snapshots(ref, cand, reference_backend="in_memory", candidate_backend="sqlite",
                                 allowed_diff=[]) == []

    def test_diff_list_length_diff(self):
        ref = _snapshot(events=[{"author": "a"}, {"author": "b"}, {"author": "c"}])
        cand = _snapshot(events=[{"author": "a"}, {"author": "b"}])
        diffs = compare_snapshots(ref, cand, reference_backend="in_memory", candidate_backend="sqlite", allowed_diff=[])
        assert len(diffs) == 1
        assert diffs[0].field_path == "events[2]"
        assert diffs[0].event_index == 2
        assert diffs[0].candidate_value == MISSING

    def test_diff_marks_allowed(self):
        ref = _snapshot(events=[{"timestamp": 1.0, "author": "user"}])
        cand = _snapshot(events=[{"timestamp": 2.0, "author": "user"}])
        rule = AllowedDiffRule(path="events[*].timestamp", reason="backend-assigned")
        diffs = compare_snapshots(ref,
                                  cand,
                                  reference_backend="in_memory",
                                  candidate_backend="sqlite",
                                  allowed_diff=[rule])
        assert len(diffs) == 1
        assert diffs[0].allowed is True
        assert diffs[0].reason == "backend-assigned"


# ---------------------------------------------------------------------------
# Task 5: allowed_diff
# ---------------------------------------------------------------------------


class TestAllowedDiff:

    def test_exact_path_match(self):
        rule = AllowedDiffRule(path="events[0].timestamp", reason="auto")
        assert is_allowed("events[0].timestamp", ("in_memory", "sqlite"), [rule])[0] is True
        assert is_allowed("events[0].author", ("in_memory", "sqlite"), [rule])[0] is False

    def test_index_wildcard(self):
        rule = AllowedDiffRule(path="events[*].timestamp", reason="auto")
        assert is_allowed("events[0].timestamp", ("in_memory", "sqlite"), [rule])[0] is True
        assert is_allowed("events[5].timestamp", ("in_memory", "sqlite"), [rule])[0] is True

    def test_backend_pair_filter(self):
        rule = AllowedDiffRule(path="events[*].timestamp", reason="auto", backend_pair=("in_memory", "redis"))
        assert is_allowed("events[0].timestamp", ("in_memory", "redis"), [rule])[0] is True
        assert is_allowed("events[0].timestamp", ("in_memory", "sqlite"), [rule])[0] is False

    def test_governance_rejects_too_many(self):
        rules = [AllowedDiffRule(path=f"events[{i}].timestamp", reason="r") for i in range(MAX_ALLOWED_PER_CASE + 1)]
        case = ReplayCase(case_id="x", description="d", allowed_diff=rules)
        with pytest.raises(ValueError):
            check_governance(case, total_fields=100, used_allowed=0)

    def test_governance_rejects_ratio(self):
        case = ReplayCase(case_id="x", description="d")
        with pytest.raises(ValueError):
            check_governance(case, total_fields=20, used_allowed=5)

    def test_governance_rejects_no_reason(self):
        rule = AllowedDiffRule(path="events[0].timestamp", reason="")
        case = ReplayCase(case_id="x", description="d", allowed_diff=[rule])
        with pytest.raises(ValueError):
            check_governance(case, total_fields=100, used_allowed=0)


# ---------------------------------------------------------------------------
# Task 6: summary_checks
# ---------------------------------------------------------------------------


class TestSummaryChecks:

    def test_detects_loss(self):
        ref = {"current": {"text": "s", "version": 1, "session_id": "s1"}}
        issues = check_summary_issues(ref, {"current": None}, candidate_backend="sqlite", session_id="s1")
        assert any(i.type == "loss" for i in issues)

    def test_detects_overwrite(self):
        ref = {"current": {"text": "s", "version": 3, "session_id": "s1"}}
        cand = {"current": {"text": "s", "version": 1, "session_id": "s1"}}
        issues = check_summary_issues(ref, cand, candidate_backend="sqlite", session_id="s1")
        assert any(i.type == "overwrite" for i in issues)

    def test_detects_affiliation(self):
        ref = {"current": {"text": "s", "version": 1, "session_id": "s1"}}
        cand = {"current": {"text": "s", "version": 1, "session_id": "replay-wrong-session"}}
        issues = check_summary_issues(ref, cand, candidate_backend="sqlite", session_id="s1")
        assert any(i.type == "affiliation" for i in issues)

    def test_no_issue_when_consistent(self):
        ref = {"current": {"text": "s", "version": 1, "session_id": "s1"}}
        assert check_summary_issues(ref, dict(ref), candidate_backend="sqlite", session_id="s1") == []

    def test_semantic_similarity(self):
        assert summary_text_similarity("hello world foo", "foo world hello") == 1.0
        assert summary_text_similarity("aaa", "zzz") == 0.0
        assert summary_text_similarity(None, "x") == 0.0


# ---------------------------------------------------------------------------
# Task 9: report
# ---------------------------------------------------------------------------


class TestReport:

    def test_report_schema_and_totals(self):
        results = [
            CaseResult(
                case_id="c1",
                session_id="s1",
                comparisons=[Comparison(candidate_backend="sqlite", status="match")],
            ),
            CaseResult(
                case_id="c2",
                session_id="s2",
                comparisons=[Comparison(candidate_backend="sqlite", status="mismatch", diffs=[_diff()])],
            ),
        ]
        report = build_diff_report(
            "in_memory",
            results,
            backend_statuses=[
                BackendStatus(name="sqlite", status="match"),
                BackendStatus(name="redis", status="skipped", reason="no url"),
            ],
        )
        assert report["schema_version"] == 3
        assert report["reference_backend"] == "in_memory"
        assert report["compared_backends"] == ["sqlite"]
        assert report["totals"] == {
            "cases": 2,
            "matched": 1,
            "mismatched": 1,
            "not_applicable": 0,
            "skipped": 0,
        }
        assert report["false_positive_rate"] == 0.5
        redis_status = [b for b in report["backend_statuses"] if b["name"] == "redis"][0]
        assert redis_status["reason"] == "no url"

    def test_report_locates_diff(self):
        results = [
            CaseResult(
                case_id="c1",
                session_id="s1",
                comparisons=[
                    Comparison(
                        candidate_backend="sqlite",
                        status="mismatch",
                        diffs=[
                            DiffEntry(
                                session_id="s1",
                                event_index=0,
                                summary_id=None,
                                field_path="events[0].author",
                                reference_backend="in_memory",
                                candidate_backend="sqlite",
                                reference_value="user",
                                candidate_value="assistant",
                            )
                        ],
                    )
                ],
            )
        ]
        report = build_diff_report("in_memory", results)
        diff = report["cases"][0]["comparisons"][0]["diffs"][0]
        for key in (
                "session_id",
                "event_index",
                "summary_id",
                "field_path",
                "reference_backend",
                "candidate_backend",
                "reference_value",
                "candidate_value",
        ):
            assert key in diff

    def test_report_not_applicable_single_backend(self):
        # 无 comparison 不误报 match(诚实标记,#153 vs #163)
        results = [CaseResult(case_id="c1", session_id="s1", comparisons=[])]
        report = build_diff_report("in_memory", results)
        assert report["cases"][0]["status"] == "not_applicable"
        assert report["totals"]["not_applicable"] == 1
