"""Acceptance tests for the skills code review agent example."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

from examples.skills_code_review_agent.agent.filtering import ReviewExecutionFilter
from examples.skills_code_review_agent.agent.models import SandboxRequest
from examples.skills_code_review_agent.agent.diff_parser import parse_unified_diff
from examples.skills_code_review_agent.agent.review_engine import ReviewConfig
from examples.skills_code_review_agent.agent.review_engine import run_review
from examples.skills_code_review_agent.agent.rules_engine import RuleEngine
from examples.skills_code_review_agent.agent.sandbox import SandboxRunner
from examples.skills_code_review_agent.agent.storage import ReviewStore


FIXTURES = [
    "no_issue",
    "security_issue",
    "async_resource_leak",
    "db_lifecycle_issue",
    "missing_tests",
    "duplicate_finding",
    "sandbox_failure",
    "secret_redaction",
]


SECRET_NEEDLES = [
    "sk-1234567890abcdef1234567890abcdef",
    "ghp_abcdefghijklmnopqrstuvwxyz123456",
    "AKIAIOSFODNN7EXAMPLE",
    "super-secret-password",
    "eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop.qrstuvwxyz123456",
]


def _run_fixture(tmp_path: Path, name: str):
    output_dir = tmp_path / name / "out"
    db_path = tmp_path / name / "review.sqlite3"
    return run_review(
        ReviewConfig(
            fixture=name,
            output_dir=output_dir,
            db_path=db_path,
            dry_run=True,
            fake_model=True,
            task_id=f"task-{name}",
            timeout_seconds=5,
            max_output_bytes=32768,
        )
    )


def test_public_fixtures_all_generate_reports(tmp_path: Path):
    for name in FIXTURES:
        result = _run_fixture(tmp_path, name)
        assert result.report_json_path.is_file()
        assert result.report_md_path.is_file()
        assert result.report["task_id"] == f"task-{name}"
        assert "summary" in result.report
        assert "sandbox_runs" in result.report


def test_example_keeps_quickstart_style_layout():
    example_root = Path("examples/skills_code_review_agent")
    agent_dir = example_root / "agent"

    assert (example_root / "README.md").is_file()
    assert (example_root / "run_agent.py").is_file()
    assert (agent_dir / "__init__.py").is_file()
    assert (agent_dir / "agent.py").is_file()
    assert (agent_dir / "config.py").is_file()
    assert (agent_dir / "prompts.py").is_file()
    assert (agent_dir / "tools.py").is_file()


def test_high_risk_detection_rate_and_false_positive_guard(tmp_path: Path):
    expected_categories = {
        "security_issue": {"security"},
        "async_resource_leak": {"async_resource"},
        "db_lifecycle_issue": {"db_lifecycle", "security"},
        "secret_redaction": {"sensitive_info"},
    }
    hits = 0
    total = 0
    for fixture, categories in expected_categories.items():
        report = _run_fixture(tmp_path, fixture).report
        found = {item["category"] for item in report["findings"] + report["warnings"] + report["needs_human_review"]}
        for category in categories:
            total += 1
            if category in found:
                hits += 1
    assert hits / total >= 0.8

    no_issue_report = _run_fixture(tmp_path, "no_issue").report
    assert no_issue_report["summary"]["finding_count"] == 0


def test_database_records_complete_task_bundle_by_task_id(tmp_path: Path):
    result = _run_fixture(tmp_path, "security_issue")
    store = ReviewStore(result.db_path)
    try:
        bundle = store.get_task(result.task_id)
    finally:
        store.close()

    assert bundle["task"]["status"] == "completed"
    assert bundle["sandbox_runs"]
    assert bundle["findings"]
    assert bundle["filter_intercepts"]
    assert bundle["metrics"]["tool_call_count"] >= 2
    assert bundle["report"]["task_id"] == result.task_id


def test_sandbox_failure_is_recorded_without_crashing_review(tmp_path: Path):
    result = _run_fixture(tmp_path, "sandbox_failure")
    runs = result.report["sandbox_runs"]
    assert any(run["name"] == "static-rules" and run["status"] == "failed" for run in runs)
    assert result.report["summary"]["final_conclusion"]
    assert result.report_json_path.is_file()
    assert result.report_md_path.is_file()


def test_secret_redaction_from_reports_and_database(tmp_path: Path):
    result = _run_fixture(tmp_path, "secret_redaction")
    report_text = result.report_json_path.read_text(encoding="utf-8") + result.report_md_path.read_text(encoding="utf-8")
    db_bytes = result.db_path.read_bytes().decode("utf-8", errors="ignore")
    for needle in SECRET_NEEDLES:
        assert needle not in report_text
        assert needle not in db_bytes
    assert "<REDACTED>" in report_text
    sensitive_items = [
        item for item in result.report["findings"]
        if item["category"] == "sensitive_info"
    ]
    assert len(sensitive_items) >= 4
    assert result.report["monitoring"]["redaction_count"] >= len(SECRET_NEEDLES)


def test_dry_run_completes_under_two_minutes(tmp_path: Path):
    start = time.monotonic()
    _run_fixture(tmp_path, "security_issue")
    assert time.monotonic() - start < 120


def test_high_risk_script_filter_blocks_execution(tmp_path: Path):
    result = _run_fixture(tmp_path, "security_issue")
    intercepts = result.report["filter_intercepts"]
    assert any(item["rule_id"] == "script.high_risk_command" for item in intercepts)
    high_risk_runs = [run for run in result.report["sandbox_runs"] if run["name"] == "high-risk-script-probe"]
    assert high_risk_runs
    assert high_risk_runs[0]["status"] == "filtered"
    assert high_risk_runs[0]["filter_decision"]["action"] == "needs_human_review"


def test_report_contains_required_sections(tmp_path: Path):
    result = _run_fixture(tmp_path, "security_issue")
    report = result.report
    assert report["findings"] is not None
    assert report["summary"]["severity_distribution"]
    assert report["needs_human_review"] is not None
    assert report["filter_intercepts"] is not None
    assert report["monitoring"]["sandbox_duration_ms"] >= 0
    assert report["sandbox_runs"]
    assert report["fix_recommendations"]


def test_duplicate_finding_dedupes_same_file_line_category(tmp_path: Path):
    result = _run_fixture(tmp_path, "duplicate_finding")
    items = result.report["findings"] + result.report["warnings"] + result.report["needs_human_review"]
    keys = [(item["file"], item["line"], item["category"]) for item in items]
    assert len(keys) == len(set(keys))


def test_sqlite_schema_contains_expected_tables(tmp_path: Path):
    result = _run_fixture(tmp_path, "security_issue")
    conn = sqlite3.connect(result.db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    tables = {row[0] for row in rows}
    assert {
        "review_task",
        "sandbox_run",
        "finding",
        "filter_intercept",
        "review_metric",
        "review_report",
    }.issubset(tables)


def test_review_report_json_is_valid_and_markdown_mentions_sandbox(tmp_path: Path):
    result = _run_fixture(tmp_path, "security_issue")
    loaded = json.loads(result.report_json_path.read_text(encoding="utf-8"))
    assert loaded["task_id"] == result.task_id
    markdown = result.report_md_path.read_text(encoding="utf-8")
    assert "## Sandbox Runs" in markdown
    assert "## Filter Intercepts" in markdown


def test_sandbox_timeout_and_output_limit_are_enforced(tmp_path: Path):
    skill_dir = Path("examples/skills_code_review_agent/skills/code-review").resolve()
    sandbox = SandboxRunner(
        runtime="dry-run-local",
        skill_dir=skill_dir,
        execution_filter=ReviewExecutionFilter(max_timeout_seconds=1, max_output_bytes=64),
    )

    timeout_run = sandbox.run(
        SandboxRequest(
            name="timeout",
            command=["$PYTHON", "-c", "import time; time.sleep(2)"],
            display_command="python -c sleep",
            cwd=".",
            timeout_seconds=1,
            max_output_bytes=64,
        )
    )
    assert timeout_run.status == "timed_out"
    assert timeout_run.timed_out is True
    assert timeout_run.error_type == "TimeoutExpired"

    output_run = sandbox.run(
        SandboxRequest(
            name="large-output",
            command=["$PYTHON", "-c", "print('x' * 200)"],
            display_command="python -c large-output",
            cwd=".",
            timeout_seconds=1,
            max_output_bytes=64,
        )
    )
    assert output_run.status == "succeeded"
    assert output_run.output_truncated is True
    assert "[output truncated]" in output_run.stdout


def test_repo_path_and_path_list_inputs_are_supported(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "review@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Review Bot"], cwd=repo, check=True)
    app_dir = repo / "app"
    app_dir.mkdir()
    target = app_dir / "calc.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    target.write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def run_expr(expr):\n    return eval(expr)\n",
        encoding="utf-8",
    )

    repo_result = run_review(
        ReviewConfig(
            repo_path=repo,
            output_dir=tmp_path / "repo-out",
            db_path=tmp_path / "repo.sqlite3",
            dry_run=True,
            fake_model=True,
            task_id="repo-path-task",
            include_high_risk_probe=False,
        )
    )
    assert repo_result.report["diff_summary"]["file_count"] == 1
    assert any(item["category"] == "security" for item in repo_result.report["findings"])

    path_list = tmp_path / "paths.txt"
    path_list.write_text("app/calc.py\n", encoding="utf-8")
    path_result = run_review(
        ReviewConfig(
            repo_path=repo,
            path_list_file=path_list,
            output_dir=tmp_path / "path-list-out",
            db_path=tmp_path / "path-list.sqlite3",
            dry_run=True,
            fake_model=True,
            task_id="path-list-task",
            include_high_risk_probe=False,
        )
    )
    assert path_result.report["diff_summary"]["file_count"] == 1
    assert any(item["category"] == "security" for item in path_result.report["findings"])


def test_hidden_like_patterns_detected_without_benign_token_false_positive():
    diff_text = """diff --git a/app/risky.py b/app/risky.py
--- a/app/risky.py
+++ b/app/risky.py
@@ -1,2 +1,8 @@
 def handler(user_id, user_cmd, user):
-    return None
+    os.system(user_cmd)
+    cursor.execute("select * from users where id=" + user_id)
+    client = httpx.AsyncClient()
+    response = requests.get("https://internal", verify=False)
+    token = issue_token(user)
+    return response
"""
    findings = RuleEngine().analyze(parse_unified_diff(diff_text))
    categories = {finding.category for finding in findings}
    titles = {finding.title for finding in findings}
    assert "security" in categories
    assert "async_resource" in categories
    assert "Shell command execution introduced" in titles
    assert "SQL built with string concatenation" in titles
    assert "httpx AsyncClient is not scoped with async with" in titles
    assert not any(finding.category == "sensitive_info" for finding in findings)
