# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Store layer (issue requirement 5, acceptance criterion 3)."""

import pytest

from codereview.store import SqlReviewStore
from codereview.store.init_db import init_db


@pytest.fixture
async def store(tmp_path):
    instance = SqlReviewStore(f"sqlite+aiosqlite:///{tmp_path}/review.db")
    await instance.initialize()
    yield instance
    await instance.close()


async def test_init_db_creates_all_tables(tmp_path):
    tables = await init_db(f"sqlite+aiosqlite:///{tmp_path}/fresh.db")
    assert tables == ["cr_filter_event", "cr_finding", "cr_report", "cr_review_task", "cr_sandbox_run"]
    # idempotent re-run (init + forward migration must not fail)
    assert await init_db(f"sqlite+aiosqlite:///{tmp_path}/fresh.db") == tables


async def test_task_roundtrip_and_status_lifecycle(store):
    await store.create_task("task-1", "fixture", "security_issue.diff", {"model_mode": "fake"})
    task = await store.get_task("task-1")
    assert task["status"] == "pending"
    assert task["input_type"] == "fixture"
    assert task["config"] == {"model_mode": "fake"}

    await store.update_task("task-1", status="running")
    await store.update_task("task-1", status="completed", diff_summary={"file_count": 2})
    task = await store.get_task("task-1")
    assert task["status"] == "completed"
    assert task["diff_summary"] == {"file_count": 2}

    with pytest.raises(KeyError):
        await store.update_task("missing", status="failed")


async def test_full_bundle_queryable_by_task_id(store):
    await store.create_task("task-2", "diff_file", "x.diff", {})
    await store.add_sandbox_run({
        "task_id": "task-2", "run_index": 0, "kind": "static_checks",
        "runtime_kind": "local", "cmd": "python3", "args": ["run_checks.py"],
        "duration_ms": 12.5, "exit_code": 0, "timed_out": False, "status": "ok",
        "filter_action": "allow", "filter_reasons": [],
        "stdout_excerpt": "emitted 2 finding(s)", "stderr_excerpt": "",
        "output_truncated": False, "error_type": "",
    })
    await store.add_filter_event({
        "task_id": "task-2", "stage": "sandbox_gate", "target": "run_checks.py",
        "action": "allow", "rule": "", "reasons": [],
    })
    await store.add_findings("task-2", [{
        "severity": "high", "category": "security_risk", "file": "a.py", "line": 3,
        "title": "t", "evidence": "e", "recommendation": "r", "confidence": 0.9,
        "source": "static_rule", "rule_id": "SEC001", "bucket": "finding",
        "dedup_key": "a.py:3:security_risk",
    }])
    await store.save_report("task-2", {
        "summary": "ok", "findings_total": 1,
        "severity_stats": {"high": 1}, "filter_summary": {"blocked": 0},
        "sandbox_summary": {"total_runs": 1}, "metrics": {"total_duration_ms": 100},
        "report": {"task_id": "task-2"},
    })

    bundle = await store.get_task_bundle("task-2")
    assert bundle["task"]["id"] == "task-2"
    assert len(bundle["sandbox_runs"]) == 1
    assert bundle["sandbox_runs"][0]["status"] == "ok"
    assert len(bundle["filter_events"]) == 1
    assert len(bundle["findings"]) == 1
    assert bundle["findings"][0]["dedup_key"] == "a.py:3:security_risk"
    assert bundle["report"]["metrics"] == {"total_duration_ms": 100}
    assert bundle["report"]["report"] == {"task_id": "task-2"}

    # unknown task id → empty bundle, no exception
    empty = await store.get_task_bundle("nope")
    assert empty["task"] is None and empty["findings"] == []


async def test_findings_isolated_per_task(store):
    await store.create_task("task-a", "fixture", "a", {})
    await store.create_task("task-b", "fixture", "b", {})
    finding = {
        "severity": "low", "category": "missing_tests", "file": "f.py", "line": 1,
        "title": "t", "evidence": "e", "recommendation": "r", "confidence": 0.9,
        "source": "static_rule", "rule_id": "TST001", "bucket": "finding", "dedup_key": "k",
    }
    await store.add_findings("task-a", [dict(finding)])
    await store.add_findings("task-b", [dict(finding), dict(finding)])
    assert len(await store.get_findings("task-a")) == 1
    assert len(await store.get_findings("task-b")) == 2


async def test_list_tasks_ordering_and_limit(store):
    for index in range(3):
        await store.create_task(f"task-{index}", "fixture", f"ref-{index}", {})
    tasks = await store.list_tasks(limit=2)
    assert len(tasks) == 2


def test_backend_swap_is_url_only():
    """The swappable-backend contract: only the URL names the engine."""
    store = SqlReviewStore("sqlite+aiosqlite:///whatever.db")
    assert store.db_url.startswith("sqlite+aiosqlite")
    # MySQL/PostgreSQL constructors accept the same shape (no code change);
    # engine creation is deferred, so instantiation alone must not connect.
    SqlReviewStore("mysql+aiomysql://user:pw@host/db")
    SqlReviewStore("postgresql+asyncpg://user:pw@host/db")
