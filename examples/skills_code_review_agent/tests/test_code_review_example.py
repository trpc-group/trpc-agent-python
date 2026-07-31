# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public fixture regression tests for the skills code-review example."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent import FindingCategory
from examples.skills_code_review_agent.agent import InputType
from examples.skills_code_review_agent.agent import ReviewPipelineConfig
from examples.skills_code_review_agent.agent import ReviewStore
from examples.skills_code_review_agent.agent import RuntimeKind
from examples.skills_code_review_agent.agent import SandboxStatus
from examples.skills_code_review_agent.agent import dedupe_findings
from examples.skills_code_review_agent.agent import parse_fixture
from examples.skills_code_review_agent.agent import run_review_pipeline
from examples.skills_code_review_agent.agent import run_review_rules
from examples.skills_code_review_agent.tests.secret_samples import generic_api_key_value
from examples.skills_code_review_agent.tests.secret_samples import generic_bearer_value
from examples.skills_code_review_agent.tests.secret_samples import generic_password_value


@pytest.mark.parametrize(
    ("fixture", "category"),
    (
        ("clean", None),
        ("security", FindingCategory.SECURITY),
        ("async_leak", FindingCategory.ASYNC),
        ("db_lifecycle", FindingCategory.DB),
        ("missing_tests", FindingCategory.TEST),
        ("duplicate", FindingCategory.SECURITY),
        ("sandbox_failure", None),
        ("secret", FindingCategory.SECRET),
    ),
)
def test_public_fixture_generates_complete_review(fixture: str, category: FindingCategory | None, tmp_path: Path):
    output_dir = tmp_path / fixture
    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref=fixture,
            output_dir=output_dir,
            db_path=output_dir / "review.sqlite3",
        ))

    assert result.task.status.value == "done"
    assert result.report.task_id == result.task.id
    assert result.report.metrics.total_duration_ms >= 0

    with ReviewStore(output_dir / "review.sqlite3") as store:
        assert store.get_task(result.task.id)["status"] == "done"
        assert store.get_input_summary(result.task.id)["task_id"] == result.task.id
        assert store.get_report(result.task.id)["report_json"]["task_id"] == result.task.id
        assert store.list_sandbox_runs(result.task.id)

    if category is not None:
        all_findings = [*result.findings, *result.warnings, *result.needs_human_review]
        assert category in {finding.category for finding in all_findings}

    if fixture == "clean":
        assert result.findings == []
        assert result.warnings == []
    elif fixture == "missing_tests":
        assert any(item.category is FindingCategory.TEST for item in result.warnings)
        assert all(item.category is not FindingCategory.TEST for item in result.findings)
    elif fixture == "duplicate":
        rule_findings = run_review_rules(parse_fixture(fixture, task_id="dedupe-check"))
        assert len(rule_findings) > len(dedupe_findings(rule_findings))


def test_sandbox_failure_fixture_is_persisted_and_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["python", "rule_runner.py"], timeout=1, output="password=secret-value")

    monkeypatch.setattr("examples.skills_code_review_agent.agent.sandbox.subprocess.run", raise_timeout)
    output_dir = tmp_path / "sandbox-failure"
    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref="sandbox_failure",
            output_dir=output_dir,
            db_path=output_dir / "review.sqlite3",
            runtime=RuntimeKind.LOCAL_DEV,
            allow_local=True,
            timeout_sec=1,
        ))

    assert result.sandbox_runs[0].status is SandboxStatus.TIMEOUT
    assert any(item.category is FindingCategory.SANDBOX for item in result.needs_human_review)
    assert "secret-value" not in json.dumps(result.report.to_dict())
    with ReviewStore(output_dir / "review.sqlite3") as store:
        sandbox_rows = store.list_sandbox_runs(result.task.id)
        assert sandbox_rows[0]["status"] == "timeout"
        assert "secret-value" not in json.dumps(sandbox_rows)


def test_secret_fixture_redacts_every_persisted_boundary(tmp_path: Path):
    output_dir = tmp_path / "secret"
    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref="secret",
            output_dir=output_dir,
            db_path=output_dir / "review.sqlite3",
        ))
    assert any(item.category is FindingCategory.SECRET for item in result.findings)

    report_text = json.dumps(result.report.to_dict())
    assert generic_api_key_value() not in report_text
    assert generic_password_value() not in report_text
    assert generic_bearer_value() not in report_text

    with ReviewStore(output_dir / "review.sqlite3") as store:
        rows = store.list_findings(result.task.id)
        assert rows
        assert generic_api_key_value() not in json.dumps(rows)
        assert generic_password_value() not in json.dumps(rows)


def test_public_fixture_parser_preserves_added_line_locations():
    summary = parse_fixture("security", task_id="parser-check")
    assert summary.file_count == 1
    assert summary.changed_files[0].candidate_lines == [3, 4, 5, 6]
    assert summary.changed_files[0].hunks[0].lines[-1].new_line == 6
