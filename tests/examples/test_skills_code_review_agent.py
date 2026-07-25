#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for the Skills-based code review agent example."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent.core import InputResolver
from examples.skills_code_review_agent.agent.core import ReviewReport
from examples.skills_code_review_agent.agent.core import normalize_findings
from examples.skills_code_review_agent.agent.governance import ReviewExecutionFilter
from examples.skills_code_review_agent.agent.storage import SqlReviewStore
from examples.skills_code_review_agent.agent.workflow import CodeReviewAgent
from examples.skills_code_review_agent.agent.workflow import ReviewConfig

EXAMPLE_ROOT = Path(__file__).parents[2] / "examples" / "skills_code_review_agent"
FIXTURES = EXAMPLE_ROOT / "tests" / "fixtures"

EXPECTED_CATEGORIES = {
    "clean.diff": set(),
    "security.diff": {"security"},
    "async_resource_leak.diff": {"async_error", "resource_leak"},
    "database_lifecycle.diff": {"database_lifecycle"},
    "missing_tests.diff": {"test_coverage"},
    "duplicate_finding.diff": {"security"},
    "sandbox_failure.diff": set(),
    "secrets.diff": {"sensitive_data"},
}


async def _review(tmp_path: Path, fixture_name: str, **overrides):
    output_dir = tmp_path / fixture_name.removesuffix(".diff")
    db_path = tmp_path / "reviews.db"
    values = {
        "runtime": "local",
        "db_url": f"sqlite:///{db_path}",
        "output_dir": output_dir,
        "skill_root": EXAMPLE_ROOT / "skills",
        "work_root": tmp_path / "workspaces",
        "dry_run": True,
        "fake_model": True,
    }
    values.update(overrides)
    agent = CodeReviewAgent(ReviewConfig(**values))
    report = await agent.review(diff_file=FIXTURES / fixture_name)
    return report, db_path, output_dir


@pytest.mark.parametrize("fixture_name", EXPECTED_CATEGORIES)
@pytest.mark.asyncio
async def test_public_diff_fixtures_generate_reports(tmp_path, fixture_name):
    report, _, output_dir = await _review(tmp_path, fixture_name)

    categories = {item.category for item in report.findings + report.warnings}
    assert EXPECTED_CATEGORIES[fixture_name] <= categories
    assert report.status in {"completed", "completed_with_warnings"}
    assert (output_dir / "review_report.json").is_file()
    assert (output_dir / "review_report.md").is_file()
    assert report.monitoring.total_duration_ms < 120_000
    assert not list((tmp_path / "workspaces").glob("ws_*"))


@pytest.mark.asyncio
async def test_duplicate_findings_are_collapsed(tmp_path):
    report, _, _ = await _review(tmp_path, "duplicate_finding.diff")
    security_findings = [item for item in report.findings if item.category == "security"]
    keys = {(item.file, item.line, item.category) for item in security_findings}

    assert len(security_findings) == len(keys) == 1


def test_low_confidence_finding_is_routed_to_human_review():
    raw = {
        "severity": "medium",
        "category": "async_error",
        "file": "app/worker.py",
        "line": 12,
        "title": "Possible swallowed error",
        "evidence": "except Exception:",
        "recommendation": "Handle expected exceptions explicitly.",
        "confidence": 0.68,
        "source": "skill:ASYNC-002",
    }

    findings, warnings, _ = normalize_findings([raw])

    assert findings == []
    assert len(warnings) == 1
    assert warnings[0].source == "skill:ASYNC-002"


@pytest.mark.asyncio
async def test_sandbox_failure_is_persisted_without_crashing_review(tmp_path):
    report, db_path, _ = await _review(
        tmp_path,
        "sandbox_failure.diff",
        checker_script="scripts/missing.py",
        allowed_scripts={"scripts/review_diff.py", "scripts/missing.py"},
    )

    assert report.status == "completed_with_warnings"
    assert report.sandbox_runs[0].status == "failed"
    assert report.monitoring.exception_distribution

    store = SqlReviewStore(f"sqlite:///{db_path}")
    await store.initialize()
    stored = await store.get_review(report.task_id)
    await store.close()
    assert stored["task"]["status"] == "completed_with_warnings"
    assert stored["sandbox_runs"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_filter_denial_never_starts_sandbox(tmp_path):
    report, _, _ = await _review(
        tmp_path,
        "clean.diff",
        checker_script="../escape.py",
    )

    assert report.status == "blocked"
    assert report.filter_decisions[0].decision == "deny"
    assert report.sandbox_runs == []
    assert report.monitoring.tool_calls == 0
    assert report.monitoring.interception_count == 1


def test_filter_denies_high_risk_executable():
    policy = ReviewExecutionFilter(
        allowed_scripts={"scripts/review_diff.py"},
        env_allowlist={"PYTHONUNBUFFERED"},
        network_allowlist=set(),
        max_timeout_seconds=120,
        max_output_bytes=64 * 1024,
        decision_sink=lambda _: None,
    )

    decision = policy.evaluate({"command": "bash scripts/review_diff.py"})

    assert decision.decision == "deny"
    assert decision.rule_id == "high_risk_command"


@pytest.mark.asyncio
async def test_timeout_is_recorded_and_review_completes(tmp_path):
    report, _, _ = await _review(
        tmp_path,
        "clean.diff",
        checker_script="scripts/timeout_probe.py",
        timeout_seconds=1,
    )

    assert report.status == "completed_with_warnings"
    assert report.sandbox_runs[0].timed_out is True
    assert report.sandbox_runs[0].status == "timed_out"


@pytest.mark.asyncio
async def test_secrets_are_redacted_from_reports_and_database(tmp_path):
    report, db_path, output_dir = await _review(tmp_path, "secrets.diff")
    forbidden = (
        "sk-live-51JwQvD7R3aN8mK2pL6x",
        "correct horse battery staple",
        "json secret with spaces",
        "ghp_1234567890abcdefghijklmnop",
        "-----BEGIN PRIVATE KEY-----",
        "supersecret",
    )
    report_text = (output_dir / "review_report.json").read_text(encoding="utf-8")
    database_bytes = db_path.read_bytes()

    assert report.monitoring.redaction_count >= 3
    assert "[REDACTED]" in report_text
    for secret in forbidden:
        assert secret not in report_text
        assert secret.encode() not in database_bytes


@pytest.mark.asyncio
async def test_database_query_returns_complete_review(tmp_path):
    report, db_path, _ = await _review(tmp_path, "security.diff")
    store = SqlReviewStore(f"sqlite:///{db_path}")
    await store.initialize()
    stored = await store.get_review(report.task_id)
    await store.close()

    assert stored["task"]["id"] == report.task_id
    assert stored["sandbox_runs"]
    assert stored["filter_decisions"]
    assert stored["findings"]
    assert stored["metrics"]["finding_count"] == len(report.findings)
    assert json.loads(stored["report"]["report_json"])["task_id"] == report.task_id


@pytest.mark.asyncio
async def test_over_budget_execution_requires_human_review_without_sandbox(tmp_path):
    report, _, _ = await _review(
        tmp_path,
        "clean.diff",
        timeout_seconds=121,
        max_timeout_seconds=120,
    )

    assert report.status == "blocked"
    assert report.filter_decisions[0].decision == "needs_human_review"
    assert report.filter_decisions[0].rule_id == "timeout_budget"
    assert report.sandbox_runs == []


@pytest.mark.asyncio
async def test_over_budget_output_is_blocked_before_sandbox(tmp_path):
    report, _, _ = await _review(
        tmp_path,
        "clean.diff",
        max_output_bytes=128 * 1024,
        max_policy_output_bytes=64 * 1024,
    )

    assert report.status == "blocked"
    assert report.filter_decisions[0].decision == "needs_human_review"
    assert report.filter_decisions[0].rule_id == "output_budget"
    assert report.sandbox_runs == []


@pytest.mark.asyncio
async def test_non_allowlisted_network_is_blocked_before_sandbox(tmp_path):
    report, _, _ = await _review(
        tmp_path,
        "clean.diff",
        network_hosts=["api.github.com"],
    )

    assert report.status == "blocked"
    assert report.filter_decisions[0].rule_id == "network_not_allowed"
    assert report.sandbox_runs == []


def test_file_list_input_is_converted_to_unified_diff(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")

    resolved = InputResolver().resolve_files([first, second])

    assert resolved.input_type == "file_list"
    assert resolved.summary.file_count == 2
    assert resolved.summary.changed_line_count == 2


@pytest.mark.asyncio
async def test_plain_unified_diff_without_git_header_is_reviewed(tmp_path):
    patch = tmp_path / "plain.diff"
    patch.write_text(
        "--- a/app/runtime.py\n"
        "+++ b/app/runtime.py\n"
        "@@ -1 +1 @@\n"
        "-return values.get(expression)\n"
        "+return eval(expression)\n",
        encoding="utf-8",
    )
    config = ReviewConfig(
        runtime="local",
        db_url=f"sqlite:///{tmp_path / 'plain.db'}",
        output_dir=tmp_path / "output",
        skill_root=EXAMPLE_ROOT / "skills",
        work_root=tmp_path / "workspaces",
        dry_run=True,
    )

    report = await CodeReviewAgent(config).review(diff_file=patch)

    assert report.input_summary.files == ["app/runtime.py"]
    assert any(item.category == "security" for item in report.findings)


def test_git_workspace_input_includes_tracked_and_untracked_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    tracked = repo / "tracked.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    tracked.write_text("value = 2\n", encoding="utf-8")
    (repo / "untracked.py").write_text("new_value = 3\n", encoding="utf-8")

    resolved = InputResolver().resolve_repo(repo)

    assert resolved.input_type == "git_workspace"
    assert resolved.summary.file_count == 2
    assert set(resolved.summary.files) == {"tracked.py", "untracked.py"}


def test_git_linked_worktree_input_is_supported(tmp_path):
    repo = tmp_path / "repo"
    linked = tmp_path / "linked"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    tracked = repo / "tracked.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(linked)], check=True)
    (linked / "tracked.py").write_text("value = 2\n", encoding="utf-8")

    resolved = InputResolver().resolve_repo(linked)

    assert resolved.input_type == "git_workspace"
    assert resolved.summary.files == ["tracked.py"]


def test_sample_report_matches_output_contract():
    sample = EXAMPLE_ROOT / "sample_output" / "review_report.json"
    report = ReviewReport.model_validate_json(sample.read_text(encoding="utf-8"))

    assert report.status == "completed"
    assert report.findings[0].source == "skill:SEC-001"
    assert report.sandbox_runs[0].runtime == "container"


@pytest.mark.skipif(os.getenv("RUN_CODE_REVIEW_CONTAINER_TEST") != "1",
                    reason="set RUN_CODE_REVIEW_CONTAINER_TEST=1 for Docker E2E")
@pytest.mark.asyncio
async def test_container_runtime_end_to_end(tmp_path):
    report, _, _ = await _review(tmp_path, "security.diff", runtime="container")

    assert report.status == "completed"
    assert report.sandbox_runs[0].runtime == "container"
    assert report.sandbox_runs[0].exit_code == 0
    assert any(item.category == "security" for item in report.findings)
