# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for SQLite persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from examples.skills_code_review_agent.agent import FilterDecision
from examples.skills_code_review_agent.agent import FilterEvent
from examples.skills_code_review_agent.agent import Finding
from examples.skills_code_review_agent.agent import FindingCategory
from examples.skills_code_review_agent.agent import FindingSeverity
from examples.skills_code_review_agent.agent import FindingSource
from examples.skills_code_review_agent.agent import InputType
from examples.skills_code_review_agent.agent import ReviewReport
from examples.skills_code_review_agent.agent import ReviewMetrics
from examples.skills_code_review_agent.agent import ReviewStore
from examples.skills_code_review_agent.agent import ReviewTask
from examples.skills_code_review_agent.agent import RuntimeKind
from examples.skills_code_review_agent.agent import SandboxRun
from examples.skills_code_review_agent.agent import parse_unified_diff


def test_store_initializes_schema(tmp_path: Path):
    db_path = tmp_path / "review.sqlite3"

    with ReviewStore(db_path):
        pass

    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"review_task", "input_diff", "finding", "filter_event", "sandbox_run", "report"}.issubset(tables)


def test_store_saves_and_queries_review_by_task_id(tmp_path: Path):
    db_path = tmp_path / "review.sqlite3"
    task, input_summary, finding, event, sandbox, report = _review_objects()

    with ReviewStore(db_path) as store:
        store.save_review(
            task=task,
            input_summary=input_summary,
            findings=[finding],
            warnings=[],
            needs_human_review=[],
            filter_events=[event],
            sandbox_runs=[sandbox],
            report=report,
            report_json_path=tmp_path / "review_report.json",
            report_md_path=tmp_path / "review_report.md",
        )
        assert store.get_task(task.id)["id"] == task.id
        assert store.get_input_summary(task.id)["summary_json"]["task_id"] == task.id
        assert store.list_findings(task.id)[0]["fingerprint"] == "fp-1"
        assert store.list_filter_events(task.id)[0]["metadata_json"] == {}
        assert store.list_sandbox_runs(task.id)[0]["status"] == "success"
        assert store.get_metrics(task.id)["finding_count"] == 1
        assert store.get_report(task.id)["report_json"]["task_id"] == task.id


def test_store_does_not_duplicate_same_fingerprint_route(tmp_path: Path):
    task, input_summary, finding, event, sandbox, report = _review_objects()

    with ReviewStore(tmp_path / "review.sqlite3") as store:
        for _ in range(2):
            store.save_review(
                task=task,
                input_summary=input_summary,
                findings=[finding],
                warnings=[],
                needs_human_review=[],
                filter_events=[event],
                sandbox_runs=[sandbox],
                report=report,
                report_json_path=tmp_path / "review_report.json",
                report_md_path=tmp_path / "review_report.md",
            )
        assert len(store.list_findings(task.id)) == 1


def test_store_redacts_sensitive_text(tmp_path: Path):
    task, input_summary, finding, event, sandbox, report = _review_objects()
    finding.evidence = "password=plainsecret"

    with ReviewStore(tmp_path / "review.sqlite3") as store:
        store.save_review(
            task=task,
            input_summary=input_summary,
            findings=[finding],
            warnings=[],
            needs_human_review=[],
            filter_events=[event],
            sandbox_runs=[sandbox],
            report=report,
            report_json_path=tmp_path / "review_report.json",
            report_md_path=tmp_path / "review_report.md",
        )
        assert "plainsecret" not in store.list_findings(task.id)[0]["evidence"]


def _review_objects():
    task = ReviewTask(input_type=InputType.FIXTURE, input_ref="clean")
    input_summary = parse_unified_diff("", task_id=task.id, input_type=InputType.FIXTURE)
    finding = Finding(
        severity=FindingSeverity.HIGH,
        category=FindingCategory.SECURITY,
        file="app.py",
        line=1,
        title="Risk",
        evidence="eval(user_input)",
        recommendation="Avoid eval.",
        confidence=0.9,
        source=FindingSource.RULE,
        fingerprint="fp-1",
    )
    event = FilterEvent(task_id=task.id, decision=FilterDecision.ALLOW, reason="allowed", target="python")
    sandbox = SandboxRun(task_id=task.id, runtime=RuntimeKind.DRY_RUN, command="python")
    report = ReviewReport(
        task_id=task.id,
        findings=[finding],
        interceptions=[event],
        sandbox_runs=[sandbox],
        metrics=ReviewMetrics(finding_count=1, severity_distribution={"high": 1}),
    )
    return task, input_summary, finding, event, sandbox, report
