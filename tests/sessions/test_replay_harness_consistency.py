# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cross-backend Session / Memory / Summary replay harness tests.

Session / Memory / Summary 跨后端回放框架测试。
"""

from __future__ import annotations

import os

import pytest

from .replay.redis_support import require_replay_redis
from .replay_harness import DEFAULT_CASES_PATH
from .replay_harness import compare_snapshots
from .replay_harness import load_replay_cases
from .replay_harness import mutate_snapshot
from .replay_harness import run_replay_suite


def test_public_replay_case_catalog_has_ten_unique_cases():
    """Require exactly ten uniquely named public replay cases.

    确保公开回放目录恰好包含十条名称唯一的用例。
    """
    cases = load_replay_cases(DEFAULT_CASES_PATH)
    assert len(cases) == 10
    assert len({case.case_id for case in cases}) == 10


async def test_inmemory_and_sqlite_replay_consistency(tmp_path):
    """Verify InMemory and SQLite agree and detect all injected defects.

    验证 InMemory 与 SQLite 结果一致，并检出全部注入故障。
    """
    report_path = tmp_path / "session_memory_summary_diff_report.json"
    report = await run_replay_suite(
        report_path=report_path,
        work_dir=tmp_path / "backends",
        backend_names=["inmemory", "sqlite"],
    )

    assert report_path.exists()
    assert report["case_count"] == 10
    assert report["summary"]["consistent_cases"] == 10
    assert report["summary"]["unallowed_differences"] == 0
    assert report["summary"]["faults_detected"] == 10
    assert report["elapsed_seconds"] <= 30


async def test_inmemory_only_lightweight_mode(tmp_path):
    """Keep fixture and fault checks effective in lightweight InMemory mode.

    验证轻量 InMemory 模式仍执行 fixture 校验和故障检出。
    """
    report = await run_replay_suite(
        work_dir=tmp_path,
        backend_names=["inmemory"],
    )

    assert report["case_count"] == 10
    assert report["summary"]["consistent_cases"] == 10
    assert report["summary"]["unallowed_differences"] == 0
    assert report["summary"]["faults_detected"] == 10
    assert report["elapsed_seconds"] <= 30


async def test_summary_faults_are_all_located(tmp_path):
    """Locate Summary loss, ownership, version, and replacement defects.

    精确定位 Summary 丢失、归属、版本及覆盖关系错误。
    """
    report = await run_replay_suite(
        work_dir=tmp_path,
        backend_names=["inmemory"],
    )
    snapshots = {case["case_id"]: case["snapshots"]["inmemory"] for case in report["cases"]}

    checks = [
        ("summary_create", "drop_summary", "/summary"),
        ("summary_create", "wrong_summary_session", "/summary/session_id"),
        ("summary_update_replace", "stale_summary_version", "/summary/version"),
        ("summary_update_replace", "wrong_summary_replacement", "/summary/replaces_summary_id"),
    ]
    for case_id, mutation, expected_path in checks:
        reference = snapshots[case_id]
        candidate = mutate_snapshot(reference, mutation)
        differences = compare_snapshots(
            case_id=case_id,
            reference=reference,
            candidate=candidate,
        )
        assert differences
        assert any(difference.field_path == expected_path for difference in differences)
        assert all(difference.session_id for difference in differences)
        assert all(difference.reference_backend and difference.candidate_backend for difference in differences)


async def test_optional_redis_integration(tmp_path):
    """Compare Redis when its opt-in integration URL is configured.

    配置可选集成 URL 时，对比 Redis 后端的一致性。
    """
    # Check the actual endpoint instead of treating the mere presence of an
    # environment variable as proof that Redis is running.
    # 检查真实端点，不能仅凭环境变量存在就认为 Redis 已启动。
    require_replay_redis()
    report = await run_replay_suite(
        work_dir=tmp_path,
        backend_names=["inmemory", "redis"],
    )
    assert report["summary"]["unallowed_differences"] == 0


@pytest.mark.skipif(not os.getenv("TRPC_REPLAY_SQL_URL"), reason="TRPC_REPLAY_SQL_URL is not configured")
async def test_optional_sql_integration(tmp_path):
    """Compare a real SQL backend when its integration URL is configured.

    配置集成 URL 时，对比真实 SQL 后端的一致性。
    """
    report = await run_replay_suite(
        work_dir=tmp_path,
        backend_names=["inmemory", "sql"],
    )
    assert report["summary"]["unallowed_differences"] == 0
