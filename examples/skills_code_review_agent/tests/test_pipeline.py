# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for the end-to-end review pipeline."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from examples.skills_code_review_agent.agent import InputType
from examples.skills_code_review_agent.agent import ReviewPipelineConfig
from examples.skills_code_review_agent.agent import ReviewStore
from examples.skills_code_review_agent.agent import RuntimeKind
from examples.skills_code_review_agent.agent import SandboxStatus
from examples.skills_code_review_agent.agent import run_review_pipeline


def test_dry_run_pipeline_writes_artifacts_database_and_reports(tmp_path: Path):
    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref="clean",
            output_dir=tmp_path,
            db_path=tmp_path / "review.sqlite3",
        ))

    assert result.task.status.value == "done"
    assert result.report.task_id == result.task.id
    assert result.sandbox_runs[0].status is SandboxStatus.SUCCESS
    with ReviewStore(tmp_path / "review.sqlite3") as store:
        assert store.get_task(result.task.id)["status"] == "done"
        assert store.get_metrics(result.task.id)["finding_count"] == result.report.metrics.finding_count
        assert store.get_report(result.task.id)["report_json"]["task_id"] == result.task.id


def test_container_pipeline_records_human_review_without_real_container(tmp_path: Path):
    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref="clean",
            output_dir=tmp_path,
            db_path=tmp_path / "review.sqlite3",
            runtime=RuntimeKind.CONTAINER,
            docker_base_url="unix:///tmp/skills-code-review-agent-missing-docker.sock",
        ))

    assert result.sandbox_runs[0].status is SandboxStatus.NEEDS_HUMAN_REVIEW
    assert result.needs_human_review
    assert result.report.needs_human_review


def test_sandbox_timeout_does_not_crash_pipeline(tmp_path: Path, monkeypatch):

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=1, output="password=plainsecret")

    monkeypatch.setattr("examples.skills_code_review_agent.agent.sandbox.subprocess.run", raise_timeout)
    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref="clean",
            output_dir=tmp_path,
            db_path=tmp_path / "review.sqlite3",
            runtime=RuntimeKind.LOCAL_DEV,
            allow_local=True,
            timeout_sec=1,
        ))

    assert result.sandbox_runs[0].status is SandboxStatus.TIMEOUT
    report_text = json.dumps(result.report.to_dict())
    assert "plainsecret" not in report_text
    with ReviewStore(tmp_path / "review.sqlite3") as store:
        assert store.get_metrics(result.task.id)["exception_distribution_json"]["TimeoutExpired"] == 1
        assert store.get_report(result.task.id)


def test_pipeline_uses_injected_store_factory(tmp_path: Path):
    saved: list[dict] = []

    class FakeStore:

        def __init__(self, db_path):
            self.db_path = db_path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def save_review(self, **kwargs):
            saved.append(kwargs)

    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref="clean",
            output_dir=tmp_path,
            db_path=tmp_path / "custom.sql",
            store_factory=FakeStore,
        ))

    assert saved
    assert saved[0]["task"].id == result.task.id
    assert saved[0]["report"].task_id == result.task.id
