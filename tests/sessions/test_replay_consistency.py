# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay 一致性 E2E:同一组 case 驱动多后端,比较事件/state/memory/summary。

轻量模式默认 InMemory vs SQLite(:memory:);Redis 经 TRPC_REPLAY_REDIS_URL 启用。
报告产物:tests/sessions/session_memory_summary_diff_report.json。
"""

from __future__ import annotations

import time
from pathlib import Path

from tests.sessions.replay.backends import enabled_backends
from tests.sessions.replay.backends import in_memory_backend
from tests.sessions.replay.backends import sqlite_backend
from tests.sessions.replay.comparator import compare_snapshots
from tests.sessions.replay.harness import load_cases
from tests.sessions.replay.harness import replay_case
from tests.sessions.replay.normalizer import normalize_snapshot
from tests.sessions.replay.report import CaseResult
from tests.sessions.replay.report import Comparison
from tests.sessions.replay.report import build_diff_report
from tests.sessions.replay.report import write_report
from tests.sessions.replay.summary_checks import check_summary_issues

CASES_DIR = str(Path(__file__).parent / "replay" / "replay_cases")
REPORT_PATH = str(Path(__file__).parent / "session_memory_summary_diff_report.json")
LIGHTWEIGHT_TIMEOUT = 30  # 验收第 6 条:轻量模式 ≤30s

KNOWN_DRIFT = {"summary_update", "summary_truncation"}
"""已知 SQLite summary 持久化漂移:``create_session_summary`` 后 SQLite ``get_session``
读回的 events 顺序 / historical_events / summary 与 InMemory 不一致(类 issue #163 的
summarizer 锚点 timestamp 问题)。框架正确发现,按设计 §8「只报告不改」记录,
不计入误报率分母,修 bug 另开 issue/PR。"""


def _find(case_id: str):
    for c in load_cases(CASES_DIR):
        if c.case_id == case_id:
            return c
    raise AssertionError(f"case not found: {case_id}")


# ---------------------------------------------------------------------------
# 冒烟:replay_case 驱动单后端
# ---------------------------------------------------------------------------


class TestReplaySmoke:

    async def test_single_turn_in_memory(self):
        snap = await replay_case(in_memory_backend(), _find("single_turn"))
        assert snap.session_id == "sess-single"
        assert len(snap.events) >= 2

    async def test_state_overwrite_cross_backend(self):
        case = _find("state_overwrite")
        snap_im = await replay_case(in_memory_backend(), case)
        snap_sql = await replay_case(sqlite_backend(), case)
        assert snap_im.state.get("counter") == 3
        assert snap_im.state == snap_sql.state

    async def test_multi_turn_in_memory_vs_sqlite_no_diff(self):
        case = _find("multi_turn")
        snap_im = normalize_snapshot(await replay_case(in_memory_backend(), case))
        snap_sql = normalize_snapshot(await replay_case(sqlite_backend(), case))
        diffs = compare_snapshots(
            snap_im,
            snap_sql,
            reference_backend="in_memory",
            candidate_backend="sqlite",
            allowed_diff=case.allowed_diff,
        )
        assert [d for d in diffs if not d.allowed] == []


# ---------------------------------------------------------------------------
# 主 E2E:全 case × 多后端 + 报告(验收 1/3/5/6)
# ---------------------------------------------------------------------------


class TestReplayConsistencyE2E:

    async def test_all_cases_cross_backend(self):
        start = time.time()
        cases = load_cases(CASES_DIR)
        backends, statuses = enabled_backends()
        reference = backends[0]
        candidates = backends[1:]

        case_results: list[CaseResult] = []
        for case in cases:
            snap_ref = normalize_snapshot(await replay_case(reference, case))
            comparisons: list[Comparison] = []
            for cand in candidates:
                snap_cand = normalize_snapshot(await replay_case(cand, case))
                diffs = compare_snapshots(
                    snap_ref,
                    snap_cand,
                    reference_backend=reference.name,
                    candidate_backend=cand.name,
                    allowed_diff=case.allowed_diff,
                )
                issues = check_summary_issues(
                    snap_ref.summary,
                    snap_cand.summary,
                    candidate_backend=cand.name,
                    session_id=snap_ref.session_id,
                )
                real = [d for d in diffs if not d.allowed]
                status = "mismatch" if (real or issues) else "match"
                comparisons.append(
                    Comparison(
                        candidate_backend=cand.name,
                        status=status,
                        diffs=diffs,
                        summary_issues=issues,
                    ))
            case_results.append(
                CaseResult(case_id=case.case_id, session_id=snap_ref.session_id, comparisons=comparisons))

        # 正常 case(排除已知 drift —— 后者是框架发现的真 bug),用于下方误报率断言;
        # 误报率本身由 build_diff_report(known_drift_cases=...) 内部计算(设计 §6)。
        normal = [cr for cr in case_results if cr.case_id not in KNOWN_DRIFT]

        report = build_diff_report(reference.name, case_results, statuses, known_drift_cases=sorted(KNOWN_DRIFT))
        write_report(report, REPORT_PATH)

        # 验收 1:InMemory + 持久化(SQLite)对比。
        assert "sqlite" in report["compared_backends"]
        # 验收 3:正常 case 误报率 0(build_diff_report 已排除 drift,直接断言其输出)。
        bad = [cr.case_id for cr in normal if any(c.status == "mismatch" for c in cr.comparisons)]
        assert report["false_positive_rate"] == 0.0, f"normal-case FPR>0: {bad}"
        for cr in normal:
            for comp in cr.comparisons:
                assert comp.status == "match", (f"unexpected mismatch in {cr.case_id}: "
                                                f"{[d.field_path for d in comp.diffs if not d.allowed]}")
        # 已知 drift case:框架应检出 SQLite 漂移(这正是框架的价值)。
        for cr in case_results:
            if cr.case_id in KNOWN_DRIFT:
                sqlite_comp = [c for c in cr.comparisons if c.candidate_backend == "sqlite"]
                assert sqlite_comp and sqlite_comp[0].status == "mismatch", (
                    f"{cr.case_id} should be detected as drift")
        # 验收 5:报告 schema。
        assert report["schema_version"] == 3
        assert report["totals"]["cases"] == len(cases)
        # 验收 6:轻量模式 ≤30s。
        elapsed = time.time() - start
        assert elapsed < LIGHTWEIGHT_TIMEOUT, f"lightweight too slow: {elapsed:.1f}s"
