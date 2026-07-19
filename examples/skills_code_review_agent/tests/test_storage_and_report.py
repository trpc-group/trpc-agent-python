"""Storage and report integration tests for the code review example."""

from __future__ import annotations

import json
from pathlib import Path

from examples.skills_code_review_agent.agent.agent import run_review_task
from examples.skills_code_review_agent.agent.config import ReviewAgentConfig
from examples.skills_code_review_agent.src.storage.repository import ReviewRepository

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_run_review_task_writes_report_files_and_database(tmp_path: Path) -> None:
    """Running the pipeline should persist records and write both report artifacts."""

    output_dir = tmp_path / "outputs"
    db_path = tmp_path / "review.db"
    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "security_issue.diff"),
        output_dir=output_dir,
        db_path=db_path,
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    json_path = output_dir / "review_report.json"
    markdown_path = output_dir / "review_report.md"
    assert json_path.exists()
    assert markdown_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["task_id"] == task.task_id
    assert payload["conclusion"] == "fail"
    assert payload["findings"]

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Review Report" in markdown
    assert "## Findings" in markdown
    assert "## Monitoring" in markdown

    repository = ReviewRepository(db_path)
    bundle = repository.get_review_bundle(task.task_id)
    assert bundle["task"]["task_id"] == task.task_id
    assert bundle["input"]["changed_files_count"] == 2
    assert len(bundle["findings"]) >= 2
    assert bundle["report"]["final_verdict"] == report.conclusion.value


def test_run_review_task_persists_human_review_state(tmp_path: Path) -> None:
    """Missing-test scenarios should persist a needs-human-review conclusion."""

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "missing_tests.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    repository = ReviewRepository(tmp_path / "review.db")
    bundle = repository.get_review_bundle(task.task_id)
    assert report.conclusion.value == "needs_human_review"
    assert bundle["report"]["final_verdict"] == "needs_human_review"
    assert bundle["task"]["status"] == "completed"


def test_secret_values_are_redacted_in_reports_and_database(tmp_path: Path) -> None:
    """Secrets must be redacted before report generation and persistence."""

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "secret_redaction.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        dry_run=True,
        fake_model=True,
    )

    task, _report = run_review_task(config)

    json_payload = json.loads(
        (tmp_path / "outputs" / "review_report.json").read_text(encoding="utf-8")
    )
    markdown = (tmp_path / "outputs" / "review_report.md").read_text(encoding="utf-8")
    bundle = ReviewRepository(tmp_path / "review.db").get_review_bundle(task.task_id)

    forbidden_fragments = [
        "sk-test-1234567890abcdef",
        "Bearer super-secret-token-value",
        "super-secret-token-value",
    ]
    joined_db_text = json.dumps(bundle, ensure_ascii=False)
    for fragment in forbidden_fragments:
        assert fragment not in json.dumps(json_payload, ensure_ascii=False)
        assert fragment not in markdown
        assert fragment not in joined_db_text


def test_filter_denies_forbidden_paths_and_skips_sandbox(tmp_path: Path) -> None:
    """Forbidden paths should be denied before any sandbox script executes."""

    diff_path = tmp_path / "forbidden.diff"
    diff_path.write_text(
        """diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -0,0 +1 @@
+API_KEY="sk-test-unsafe"
""",
        encoding="utf-8",
    )

    config = ReviewAgentConfig(
        diff_file=str(diff_path),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        dry_run=True,
        fake_model=True,
    )

    task, _report = run_review_task(config)

    assert task.filter_decisions
    assert all(decision.decision.value == "deny" for decision in task.filter_decisions)
    assert task.sandbox_runs
    assert all(run.status.value == "blocked" for run in task.sandbox_runs)


def test_sandbox_failure_is_recorded_without_crashing_task(tmp_path: Path) -> None:
    """Sandbox failures should be recorded as findings while the review still completes."""

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "sandbox_failure.diff"),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    assert task.status.value == "completed"
    assert any(run.status.value == "failed" for run in task.sandbox_runs)
    assert any(finding.category.value == "sandbox" for finding in task.findings)
    assert report.monitoring_summary["sandbox_run_count"] >= 1
