# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Environment-gated integration entry points for replay consistency."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from .adapters import InMemoryReplayAdapter
from .adapters import RedisReplayAdapter
from .adapters import SQLUrlReplayAdapter
from .canonicalize import canonicalize_snapshot
from .capabilities import capabilities_for
from .cases import ReplayCase
from .cases import standard_cases
from .diff import compare_snapshots
from .reporting import ReplayReport
from .reporting import build_metrics
from .reporting import write_json_report


def _run(coro):
    return asyncio.run(coro)


async def _compare_with_external(case_id: str, external_adapter):
    base_case = next(case for case in standard_cases() if case.case_id == case_id)
    case = _namespaced_case(base_case, uuid.uuid4().hex[:8])
    left = InMemoryReplayAdapter(case=case)
    right = external_adapter(case)
    await left.setup()
    await right.setup()
    try:
        left_snapshot = await left.replay()
        right_snapshot = await right.replay()
        reference = canonicalize_snapshot(left_snapshot)
        actual = canonicalize_snapshot(right_snapshot)
        return compare_snapshots(reference, actual, case_id=case.case_id, backend_pair=(left.name, right.name))
    finally:
        await left.close()
        await right.close()


def _write_integration_report(tmp_path: Path, stem: str, report: ReplayReport) -> None:
    write_json_report(tmp_path / f"{stem}.json", report)
    report_dir = os.environ.get("REPLAY_REPORT_DIR")
    if report_dir:
        write_json_report(Path(report_dir) / f"{stem}.json", report)


def _namespaced_case(case: ReplayCase, suffix: str) -> ReplayCase:
    operations = tuple(
        replace(
            op,
            app_name=f"{op.app_name}_{suffix}",
            user_id=f"{op.user_id}_{suffix}",
            session_id=f"{op.session_id}_{suffix}",
        ) for op in case.operations
    )
    return replace(case, case_id=f"{case.case_id}_{suffix}", operations=operations)


@pytest.mark.replay_integration
@pytest.mark.parametrize(
    "case_id",
    [
        "all_entities_contract",
        "state_shallow_update",
        "memory_scope_user_session",
        "summary_create_update",
    ],
)
def test_redis_integration_replay(tmp_path, case_id):
    if os.environ.get("RUN_REPLAY_REDIS_INTEGRATION") != "1" or not os.environ.get("REDIS_URL"):
        pytest.skip("Set RUN_REPLAY_REDIS_INTEGRATION=1 and REDIS_URL to run Redis replay integration")
    redis_url = os.environ["REDIS_URL"]

    def factory(case):
        return RedisReplayAdapter(case=case, redis_url=redis_url)

    diffs = _run(_compare_with_external(case_id, factory))
    metrics = build_metrics(normal_case_count=1, normal_case_pass_count=0 if diffs else 1, diffs=diffs)
    report = ReplayReport(
        case_id=f"redis_integration:{case_id}",
        backend_pair=("in_memory", "redis"),
        metrics=metrics,
        diffs=[diff.to_dict() for diff in diffs],
        capabilities=capabilities_for("in_memory", "redis"),
    )
    _write_integration_report(tmp_path, f"session_memory_summary_redis_{case_id}_integration_report", report)
    assert [diff for diff in diffs if not diff.allowed] == []


@pytest.mark.replay_integration
def test_sql_integration_replay(tmp_path):
    if os.environ.get("RUN_REPLAY_SQL_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"):
        pytest.skip("Set RUN_REPLAY_SQL_INTEGRATION=1 and DATABASE_URL to run SQL replay integration")
    database_url = os.environ["DATABASE_URL"]

    def factory(case):
        return SQLUrlReplayAdapter(case=case, db_url=database_url)

    diffs = _run(_compare_with_external("all_entities_contract", factory))
    metrics = build_metrics(normal_case_count=1, normal_case_pass_count=0 if diffs else 1, diffs=diffs)
    report = ReplayReport(
        case_id="sql_integration",
        backend_pair=("in_memory", "sql"),
        metrics=metrics,
        diffs=[diff.to_dict() for diff in diffs],
        capabilities=capabilities_for("in_memory", "sql"),
    )
    _write_integration_report(tmp_path, "session_memory_summary_sql_integration_report", report)
    assert [diff for diff in diffs if not diff.allowed] == []
