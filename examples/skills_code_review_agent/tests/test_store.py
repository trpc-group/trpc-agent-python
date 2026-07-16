# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the SQLite-backed ReviewStore."""
from review.findings import Finding
from storage.store import ReviewStore


async def _new_store(tmp_path):
    return ReviewStore(db_url=f"sqlite:///{tmp_path}/cr.db")


async def test_task_roundtrip(tmp_path):
    store = await _new_store(tmp_path)
    task_id = await store.create_task("fixture", "clean.diff", "local", True)
    assert task_id
    await store.update_task(task_id, status="completed",
                            diff_summary={"files_changed": 1}, finished=True)
    bundle = await store.get_task_bundle(task_id)
    assert bundle["task"]["status"] == "completed"
    assert bundle["task"]["diff_summary"]["files_changed"] == 1
    assert bundle["task"]["dry_run"] is True
    await store.close()


async def test_bundle_collects_all_tables(tmp_path):
    store = await _new_store(tmp_path)
    task_id = await store.create_task("fixture", "x.diff", "local", True)
    await store.add_sandbox_run(task_id, script="check_security.py", category="security",
                                status="ok", exit_code=0, duration_ms=12, timed_out=False,
                                stdout_summary="{}", stderr_summary="", error_type="")
    await store.add_findings(task_id, [Finding(
        severity="critical", category="secret_leak", file="a.py", line=3,
        title="secret", evidence='key = "sk-abcdefghijklmnopqrstuvwxyz123456"',
        confidence=0.95)], status="reported")
    await store.add_filter_event(task_id, "rm -rf /", "deny", "risk_command", "destructive command")
    await store.add_metrics(task_id, {"total_duration_ms": 1000, "sandbox_duration_ms": 500,
                                      "tool_calls": 6, "intercepts": 1, "findings_total": 1,
                                      "severity_distribution": {"critical": 1},
                                      "error_distribution": {}})
    await store.add_report(task_id, {"conclusion": "blocked"}, "# report")
    bundle = await store.get_task_bundle(task_id)
    assert len(bundle["sandbox_runs"]) == 1
    assert len(bundle["findings"]) == 1
    assert len(bundle["filter_events"]) == 1
    assert bundle["metrics"]["tool_calls"] == 6
    assert bundle["report"]["report_json"]["conclusion"] == "blocked"
    await store.close()


async def test_store_redacts_evidence_and_output(tmp_path):
    store = await _new_store(tmp_path)
    task_id = await store.create_task("fixture", "x.diff", "local", True)
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    await store.add_findings(task_id, [Finding(
        severity="critical", category="secret_leak", file="a.py", line=3,
        title="secret", evidence=f'key = "{secret}"', confidence=0.95)], status="reported")
    await store.add_sandbox_run(task_id, script="s.py", category="c", status="ok",
                                exit_code=0, duration_ms=1, timed_out=False,
                                stdout_summary=f"leaked {secret}", stderr_summary="", error_type="")
    bundle = await store.get_task_bundle(task_id)
    assert secret not in bundle["findings"][0]["evidence"]
    assert secret not in bundle["sandbox_runs"][0]["stdout_summary"]
    await store.close()


async def test_unknown_task_returns_none_task(tmp_path):
    store = await _new_store(tmp_path)
    bundle = await store.get_task_bundle("nope")
    assert bundle["task"] is None
    await store.close()
