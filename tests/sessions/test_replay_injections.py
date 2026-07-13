# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""检出验证:快照层注入(对齐 10 PR)+ 端到端后端注入(本设计创新)。

- 快照层:deepcopy 改字段,验证比较器 + summary_checks 检出率(8 种,覆盖 event/
  state/memory/summary 四类)。
- 端到端:直接改 SQL 行 / Redis key 后重读,验证 harness 对真实后端漂移的感知。
对应验收第 2 条(100% 检出)与第 4 条(summary 三类 100% 检出)。
"""

from __future__ import annotations

import os

import pytest

from tests.sessions.replay.backends import in_memory_backend
from tests.sessions.replay.backends import sqlite_backend
from tests.sessions.replay.comparator import compare_snapshots
from tests.sessions.replay.harness import ReplaySnapshot
from tests.sessions.replay.harness import load_cases
from tests.sessions.replay.harness import replay_case
from tests.sessions.replay.injectors import inject_redis_diff
from tests.sessions.replay.injectors import inject_snapshot_diff
from tests.sessions.replay.injectors import inject_sql_diff
from tests.sessions.replay.normalizer import normalize_snapshot
from tests.sessions.replay.summary_checks import check_summary_issues

CASES_DIR = "tests/sessions/replay/replay_cases"

# 验收 2(字面):每条 case 配一种「该 case 结构支持」的注入,断言 100% 检出。
_CASE_INJECTION = {
    "single_turn": "event_author",
    "multi_turn": "event_text",
    "tool_round_trip": "event_author",
    "state_overwrite": "state_value",
    "memory_preference": "memory_content",
    "memory_fact_update": "memory_content",
    "summary_create": "summary_affiliation",
    "summary_update": "summary_overwrite",
    "summary_truncation": "extra_event",
    "retry_recovery": "extra_event",
}


def _find(case_id: str):
    for c in load_cases(CASES_DIR):
        if c.case_id == case_id:
            return c
    raise AssertionError(f"case not found: {case_id}")


def _detected(base: ReplaySnapshot, injected: ReplaySnapshot) -> bool:
    diffs = compare_snapshots(
        base,
        injected,
        reference_backend="in_memory",
        candidate_backend="in_memory",
        allowed_diff=[],
    )
    issues = check_summary_issues(base.summary,
                                  injected.summary,
                                  candidate_backend="in_memory",
                                  session_id=base.session_id)
    return bool([d for d in diffs if not d.allowed]) or bool(issues)


# ---------------------------------------------------------------------------
# 快照层注入:8 种 kind 全检出(验收 2 + 4)
# ---------------------------------------------------------------------------


class TestSnapshotInjection:

    async def test_all_eight_kinds_detected(self):
        base_map = {
            "single_turn": normalize_snapshot(await replay_case(in_memory_backend(), _find("single_turn"))),
            "memory_preference": normalize_snapshot(await replay_case(in_memory_backend(), _find("memory_preference"))),
            "summary_create": normalize_snapshot(await replay_case(in_memory_backend(), _find("summary_create"))),
            "state_overwrite": normalize_snapshot(await replay_case(in_memory_backend(), _find("state_overwrite"))),
        }
        # (case_id, kind) —— kind 挂在结构匹配的快照上。
        plan = [
            ("single_turn", "event_author"),
            ("single_turn", "event_text"),
            ("single_turn", "extra_event"),
            ("state_overwrite", "state_value"),
            ("memory_preference", "memory_content"),
            ("summary_create", "summary_loss"),
            ("summary_create", "summary_overwrite"),
            ("summary_create", "summary_affiliation"),
        ]
        not_detected = [
            f"{cid}/{kind}" for cid, kind in plan
            if not _detected(base_map[cid], inject_snapshot_diff(base_map[cid], kind))
        ]
        assert not_detected == [], f"injections not detected: {not_detected}"

    async def test_each_case_detects_injection(self):
        """验收 2(字面):10 条 case 各注入一种不一致,必须 100% 检出。"""
        not_detected = []
        for case in load_cases(CASES_DIR):
            kind = _CASE_INJECTION.get(case.case_id)
            if kind is None:
                continue
            base = normalize_snapshot(await replay_case(in_memory_backend(), case))
            if not _detected(base, inject_snapshot_diff(base, kind)):
                not_detected.append(f"{case.case_id}/{kind}")
        assert not_detected == [], f"injections not detected: {not_detected}"


# ---------------------------------------------------------------------------
# 端到端 SQL 注入(创新:验证真实后端漂移感知)
# ---------------------------------------------------------------------------


async def _read_sql_snapshot(db_url: str, app: str, user: str, sid: str) -> ReplaySnapshot:
    """用全新 service 读 DB 文件(绕过缓存),组装快照。"""
    backend = sqlite_backend(db_url)
    got = await backend.session_service.get_session(app_name=app, user_id=user, session_id=sid)
    return ReplaySnapshot(
        backend_name="sqlite",
        session_id=sid,
        events=[e.model_dump() for e in got.events],
        historical_events=[e.model_dump() for e in got.historical_events],
        state=dict(got.state),
    )


class TestEndToEndSqlInjection:

    async def test_event_author_drift_detected(self, tmp_path):
        db_url = f"sqlite:///{tmp_path.as_posix()}/inj.db"
        case = _find("single_turn")
        await replay_case(sqlite_backend(db_url), case)

        before = normalize_snapshot(await _read_sql_snapshot(db_url, "replay-single", "u1", "sess-single"))
        assert inject_sql_diff(db_url, "replay-single", "u1", "sess-single", "event_author")
        after = normalize_snapshot(await _read_sql_snapshot(db_url, "replay-single", "u1", "sess-single"))

        diffs = compare_snapshots(
            before,
            after,
            reference_backend="sqlite",
            candidate_backend="sqlite",
            allowed_diff=[],
        )
        real = [d for d in diffs if not d.allowed]
        assert any("author" in d.field_path for d in real), f"author drift not detected: {real}"


# ---------------------------------------------------------------------------
# 端到端 Redis 注入(需要真实 Redis)
# ---------------------------------------------------------------------------


class TestEndToEndRedisInjection:

    async def test_event_author_drift_detected(self):
        redis_url = os.environ.get("TRPC_REPLAY_REDIS_URL")
        if not redis_url:
            pytest.skip("TRPC_REPLAY_REDIS_URL unset")
        from tests.sessions.replay.backends import redis_backend

        case = _find("single_turn")
        backend = redis_backend(redis_url)
        await replay_case(backend, case)

        got = await backend.session_service.get_session(app_name="replay-single",
                                                        user_id="u1",
                                                        session_id="sess-single")
        before = normalize_snapshot(
            ReplaySnapshot(
                backend_name="redis",
                session_id="sess-single",
                events=[e.model_dump() for e in got.events],
            ))
        assert inject_redis_diff(redis_url, "replay-single", "u1", "sess-single", "event_author")
        got2 = await backend.session_service.get_session(app_name="replay-single",
                                                         user_id="u1",
                                                         session_id="sess-single")
        after = normalize_snapshot(
            ReplaySnapshot(
                backend_name="redis",
                session_id="sess-single",
                events=[e.model_dump() for e in got2.events],
            ))

        diffs = compare_snapshots(
            before,
            after,
            reference_backend="redis",
            candidate_backend="redis",
            allowed_diff=[],
        )
        real = [d for d in diffs if not d.allowed]
        assert any("author" in d.field_path for d in real), f"redis drift not detected: {real}"
