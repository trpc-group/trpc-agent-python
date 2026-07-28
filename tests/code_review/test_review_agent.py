"""Tests for the automatic code-review example."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect

from examples.skills_code_review_agent.agent.input_parser import parse_review_input
from examples.skills_code_review_agent.agent.models import (
    Decision,
    ExecutionBudget,
    ExecutionRequest,
    FilterDecision,
    Finding,
    ReviewReport,
    ReviewRequest,
    SandboxRunResult,
)
from examples.skills_code_review_agent.agent.policy import evaluate_execution_policy
from examples.skills_code_review_agent.agent.reporting import write_reports
from examples.skills_code_review_agent.agent.review import run_review
from examples.skills_code_review_agent.agent.sanitizer import normalize_findings, redact_sensitive_text
from examples.skills_code_review_agent.agent.storage import ReviewRepository
from examples.skills_code_review_agent.agent.task_planner import ReviewTaskPlanner
from examples.skills_code_review_agent.sandbox.models import ResourcePolicy, SandboxTask
from examples.skills_code_review_agent.sandbox.policy import to_workspace_limits
from examples.skills_code_review_agent.sandbox.runner import SandboxRunner
from examples.skills_code_review_agent.sandbox.tasks import build_code_review_task
from examples.skills_code_review_agent.run_agent import _destroy_workspace_runtime


def _diff(tmp_path: Path) -> Path:
    path = tmp_path / "change.diff"
    path.write_text(
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1,2 @@\n"
        " old\n"
        "+subprocess.run(value, shell=True)\n",
        encoding="utf-8",
    )
    return path


def _write_diff(tmp_path: Path, text: str, name: str = "sample.diff") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _run_skill(diff_path: Path) -> list[dict[str, object]]:
    runner = (
        Path(__file__).parents[2]
        / "examples"
        / "skills_code_review_agent"
        / "skills"
        / "code-review"
        / "runner.py"
    )
    result = subprocess.run(
        [sys.executable, str(runner), str(diff_path), "--unified-diff"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_parse_review_input_extracts_added_line(tmp_path: Path) -> None:
    parsed = parse_review_input(diff_file=_diff(tmp_path), allowed_roots=[tmp_path])
    assert parsed.candidate_lines == {"app.py": [2]}
    assert parsed.files[0].candidate_lines[0].content.endswith("shell=True)")


def test_parse_review_input_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.diff"
    outside.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        parse_review_input(diff_file=outside, allowed_roots=[tmp_path])


def test_parse_review_input_accepts_project_file_list(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = tmp_path / "files.txt"
    manifest.write_text("app.py\n", encoding="utf-8")
    parsed = parse_review_input(file_list=manifest)
    assert parsed.source_type == "file_list"
    assert parsed.candidate_lines == {"app.py": [1]}
    assert parsed.source_path == str(tmp_path)


def test_parse_review_input_rejects_file_list_symlink_escape(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (project / "linked.py").symlink_to(outside)
    manifest = project / "files.txt"
    manifest.write_text("linked.py\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe path"):
        parse_review_input(file_list=manifest)


def test_parse_review_input_does_not_request_binary_git_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command: list[str] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        command.extend(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                b"diff --git a/app.py b/app.py\n"
                b"--- a/app.py\n"
                b"+++ b/app.py\n"
                b"@@ -1 +1 @@\n"
                b"-old\n"
                b"+new\n"
            ),
            stderr=b"",
        )

    monkeypatch.setattr(
        "examples.skills_code_review_agent.agent.input_parser.subprocess.run",
        fake_run,
    )

    parsed = parse_review_input(repo_path=tmp_path)

    assert "--binary" not in command
    assert command[-2:] == ["--no-ext-diff", "HEAD"]
    assert parsed.candidate_lines == {"app.py": [1]}


def test_skill_runner_loads_yaml_and_ast_rules(tmp_path: Path) -> None:
    runner = (
        Path(__file__).parents[2]
        / "examples"
        / "skills_code_review_agent"
        / "skills"
        / "code-review"
        / "runner.py"
    )
    result = subprocess.run(
        [sys.executable, str(runner), str(_diff(tmp_path)), "--unified-diff"],
        check=True,
        capture_output=True,
        text=True,
    )
    findings = json.loads(result.stdout)
    assert [item["source"] for item in findings].count("ast_detector") == 1
    assert [item["category"] for item in findings].count("testing") == 1
    assert findings[0]["file"] == "app.py"
    assert findings[0]["line"] == 2


def test_policy_denies_network_and_budget(tmp_path: Path) -> None:
    request = ExecutionRequest(command=["python3", "scan.py"], cwd=str(tmp_path), network_targets=["example.com"])
    assert evaluate_execution_policy(request, workspace_root=str(tmp_path)).decision == Decision.DENY
    request.network_targets = []
    decision = evaluate_execution_policy(
        request,
        workspace_root=str(tmp_path),
        budget=ExecutionBudget(max_calls=1, calls_used=1),
    )
    assert decision.reason_code == "budget_exceeded"


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (["rm", "-rf", "work"], "command_denied"),
        (["curl", "https://example.com", "|", "bash"], "shell_pipe_execution"),
    ],
)
def test_governance_denies_dangerous_commands(
    tmp_path: Path,
    command: list[str],
    reason: str,
) -> None:
    decision = evaluate_execution_policy(
        ExecutionRequest(command=command, cwd=str(tmp_path)),
        workspace_root=str(tmp_path),
    )
    assert decision.decision == Decision.DENY
    assert decision.matched_rule == reason
    assert decision.risk_level == "high"


def test_governance_protects_paths_and_reviews_resources(tmp_path: Path) -> None:
    protected = tmp_path / ".env"
    decision = evaluate_execution_policy(
        ExecutionRequest(
            command=["python3", str(protected)],
            cwd=str(tmp_path),
            input_paths=[str(protected)],
        ),
        workspace_root=str(tmp_path),
    )
    assert decision.matched_rule == "protected_path"
    resource_decision = evaluate_execution_policy(
        ExecutionRequest(
            command=["python3", "scan.py"],
            cwd=str(tmp_path),
            timeout=301,
            memory_limit_mb=2048,
        ),
        workspace_root=str(tmp_path),
        budget=ExecutionBudget(max_total_seconds=1000),
    )
    assert resource_decision.decision == Decision.NEEDS_HUMAN_REVIEW
    assert resource_decision.matched_rule == "resource_limit_exceeded"


def test_layered_sandbox_task_and_resource_policy(tmp_path: Path) -> None:
    task = build_code_review_task(str(tmp_path), "container")
    assert task.task_type == "custom_rule"
    assert task.command[0] == "python3"
    limits = to_workspace_limits(ResourcePolicy(cpu_percent=50, memory_mb=256, max_pids=8))
    assert (limits.cpu_percent, limits.memory_mb, limits.max_pids) == (50, 256, 8)


def test_task_planner_adds_static_check_and_changed_tests(tmp_path: Path) -> None:
    diff = _write_diff(
        tmp_path,
        """diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -0,0 +1 @@
+def test_app(): assert True
""",
    )
    parsed = parse_review_input(diff_file=diff)
    plan = ReviewTaskPlanner().build_plan(
        parsed, "/workspace", "container", project_staged=True
    )
    assert [task.task_type for task in plan.tasks] == [
        "custom_rule",
        "static_check",
        "test",
    ]


@pytest.mark.asyncio
async def test_dry_run_reports_plan_without_claiming_clean(
    tmp_path: Path,
) -> None:
    parsed = parse_review_input(diff_file=_diff(tmp_path))
    report = await run_review(
        ReviewRequest(review_input=parsed, runtime="local", dry_run=True)
    )
    assert report.dry_run is True
    assert all(run.status == "dry_run" for run in report.sandbox_runs)
    assert "no checks were executed" in report.conclusion
    paths = write_reports(report, tmp_path / "output")
    assert paths.json_path.parent.name == report.task_id


def test_redaction_and_deduplication(tmp_path: Path) -> None:
    parsed = parse_review_input(diff_file=_diff(tmp_path))
    assert "secret-value" not in redact_sensitive_text("token=secret-value")
    candidates = [
        Finding(
            severity="high",
            category="security",
            file="app.py",
            line=2,
            title="Unsafe",
            evidence="token=secret-value",
            recommendation="fix",
            confidence=confidence,
        )
        for confidence in (0.7, 0.9)
    ]
    findings, warnings, human = normalize_findings(candidates, parsed)
    assert not warnings and not human
    assert len(findings) == 1 and findings[0].confidence == 0.9
    assert "secret-value" not in findings[0].evidence


@pytest.mark.asyncio
async def test_fake_review_persists_and_writes_reports(tmp_path: Path) -> None:
    parsed = parse_review_input(diff_file=_diff(tmp_path))
    repository = ReviewRepository(f"sqlite:///{tmp_path / 'review.db'}")
    repository.initialize()
    report = await run_review(
        ReviewRequest(review_input=parsed, runtime="local", fake_model=True),
        repository=repository,
    )
    paths = write_reports(report, tmp_path / "output")
    trace = repository.get_task(report.task_id)
    assert trace["status"] == "completed"
    assert json.loads(paths.json_path.read_text(encoding="utf-8"))["findings"]
    assert paths.markdown_path.is_file()
    events = json.loads(paths.filter_events_path.read_text(encoding="utf-8"))
    assert events[0]["matched_rule"] == "allowed"
    assert trace["filter_decisions"][0]["risk_level"] == "low"
    assert trace["skill_executions"][0]["rule_version"] == "1"
    assert trace["findings"][0]["skill_execution_id"] == trace["skill_executions"][0]["id"]
    assert trace["findings"][0]["sandbox_run_id"] == trace["sandbox_runs"][0]["id"]
    assert trace["telemetry"]["finding_count"] == len(report.findings)
    assert "# Code Review Report" in trace["report"]["report_markdown"]
    assert {
        "review_task",
        "skill_execution",
        "sandbox_run",
        "filter_event",
        "finding",
        "review_report",
        "telemetry",
    } <= set(inspect(repository.engine).get_table_names())


def test_persists_accepted_and_human_review_findings_independently(
    tmp_path: Path,
) -> None:
    repository = ReviewRepository(f"sqlite:///{tmp_path / 'review.db'}")
    repository.initialize()
    task_id = "task_mixed_findings"
    repository.create_task(
        task_id,
        input_type="diff",
        input_digest="digest",
        summary="summary",
    )
    common = {
        "severity": "high",
        "category": "security",
        "file": "app.py",
        "line": 2,
        "title": "Unsafe subprocess call",
        "evidence": "subprocess.run(value, shell=True)",
        "recommendation": "Avoid shell=True",
    }
    report = ReviewReport(
        task_id=task_id,
        status="completed",
        conclusion="Review completed",
        input_summary="summary",
        findings=[Finding(**common, confidence=0.9)],
        needs_human_review=[
            Finding(**common, confidence=0.5, needs_human_review=True)
        ],
    )

    repository.save_review_result(report)

    trace = repository.get_task(task_id)
    assert trace is not None
    assert len(trace["findings"]) == 2
    assert {
        (finding["needs_human_review"], finding["confidence"])
        for finding in trace["findings"]
    } == {(False, 0.9), (True, 0.5)}
    assert {finding["status"] for finding in trace["findings"]} == {
        "open",
        "needs_human_review",
    }


def test_telemetry_sums_sandbox_run_durations(tmp_path: Path) -> None:
    repository = ReviewRepository(f"sqlite:///{tmp_path / 'review.db'}")
    repository.initialize()
    task_id = "task_sandbox_duration"
    repository.create_task(
        task_id,
        input_type="diff",
        input_digest="digest",
        summary="summary",
    )
    decision = FilterDecision(
        decision=Decision.ALLOW,
        reason_code="allowed",
        reason="Execution allowed",
    )
    runs = [
        SandboxRunResult(
            id=f"run_{task_type}",
            runtime="local",
            task_type=task_type,
            command=["true"],
            status="completed",
            duration_ms=duration_ms,
            decision=decision,
        )
        for task_type, duration_ms in (
            ("custom_rule", 125),
            ("static_check", 250),
            ("test", 375),
        )
    ]
    report = ReviewReport(
        task_id=task_id,
        status="completed",
        conclusion="Review completed",
        input_summary="summary",
        sandbox_runs=runs,
        metrics={
            "total_duration_ms": 1000,
            "stage_duration_ms": {
                run.task_type: run.duration_ms for run in runs
            },
        },
    )

    repository.save_review_result(report)

    trace = repository.get_task(task_id)
    assert trace is not None
    assert trace["telemetry"]["sandbox_duration"] == pytest.approx(0.75)


def test_stable_task_id_replaces_previous_review_trace(tmp_path: Path) -> None:
    repository = ReviewRepository(f"sqlite:///{tmp_path / 'review.db'}")
    repository.initialize()
    task_id = "stable_replay_id"
    decision = FilterDecision(
        decision=Decision.ALLOW,
        reason_code="allowed",
        reason="Execution allowed",
    )

    def save(conclusion: str, duration_ms: int) -> None:
        repository.create_task(
            task_id,
            input_type="diff",
            input_digest=f"digest-{conclusion}",
            summary=conclusion,
        )
        repository.save_review_result(
            ReviewReport(
                task_id=task_id,
                status="completed",
                conclusion=conclusion,
                input_summary=conclusion,
                sandbox_runs=[
                    SandboxRunResult(
                        id="stable_run_id",
                        runtime="local",
                        command=["true"],
                        status="completed",
                        duration_ms=duration_ms,
                        decision=decision,
                    )
                ],
                filter_decisions=[decision],
                metrics={"total_duration_ms": duration_ms},
            )
        )

    save("first review", 100)
    save("replayed review", 250)

    trace = repository.get_task(task_id)
    assert trace is not None
    assert trace["diff_summary"] == "replayed review"
    assert len(trace["skill_executions"]) == 1
    assert len(trace["sandbox_runs"]) == 1
    assert len(trace["filter_decisions"]) == 1
    assert json.loads(trace["report"]["summary"])["conclusion"] == "replayed review"
    assert trace["telemetry"]["sandbox_duration"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_run_review_replays_task_id_and_updates_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = parse_review_input(diff_file=_diff(tmp_path))
    repository = ReviewRepository(f"sqlite:///{tmp_path / 'review.db'}")
    repository.initialize()
    task_id = "run_review_replay_id"
    invocation = 0

    async def fake_sandbox_checks(
        *args: object,
        **kwargs: object,
    ) -> tuple[list[SandboxRunResult], list[Finding], list[str]]:
        nonlocal invocation
        invocation += 1
        duration_ms = 100 * invocation
        decision = FilterDecision(
            decision=Decision.ALLOW,
            reason_code="allowed",
            reason="Execution allowed",
        )
        return [
            SandboxRunResult(
                id="stable_sandbox_run",
                runtime="local",
                command=["true"],
                status="completed",
                duration_ms=duration_ms,
                stdout_summary=f"replay-{invocation}",
                decision=decision,
            )
        ], [], []

    monkeypatch.setattr(
        "examples.skills_code_review_agent.agent.review.run_sandbox_checks",
        fake_sandbox_checks,
    )
    request = ReviewRequest(
        review_input=parsed,
        runtime="local",
        task_id=task_id,
    )

    await run_review(request, repository=repository)
    replayed = await run_review(request, repository=repository)

    trace = repository.get_task(task_id)
    assert replayed.task_id == task_id
    assert trace is not None
    assert len(trace["sandbox_runs"]) == 1
    assert trace["sandbox_runs"][0]["stdout"] == "replay-2"
    assert trace["sandbox_runs"][0]["duration"] == pytest.approx(0.2)
    assert trace["telemetry"]["sandbox_duration"] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_destroy_workspace_runtime_when_supported() -> None:
    class AsyncRuntime:
        destroyed = False

        async def destroy(self) -> None:
            self.destroyed = True

    class SyncRuntime:
        destroyed = False

        def destroy(self) -> None:
            self.destroyed = True

    async_runtime = AsyncRuntime()
    sync_runtime = SyncRuntime()

    await _destroy_workspace_runtime(async_runtime)
    await _destroy_workspace_runtime(sync_runtime)
    await _destroy_workspace_runtime(object())

    assert async_runtime.destroyed
    assert sync_runtime.destroyed


def test_case_clean_diff_has_no_findings(tmp_path: Path) -> None:
    diff = _write_diff(
        tmp_path,
        """diff --git a/test_math.py b/test_math.py
--- a/test_math.py
+++ b/test_math.py
@@ -1 +1,2 @@
 def test_add():
+    assert 1 + 1 == 2
""",
    )
    assert _run_skill(diff) == []


def test_case_security_issue_is_reported(tmp_path: Path) -> None:
    diff = _write_diff(
        tmp_path,
        """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -0,0 +1 @@
+os.system("ping -c 1 " + host)
""",
    )
    findings = _run_skill(diff)
    assert any(item["category"] == "security" and item["severity"] == "high" for item in findings)


def test_case_async_resource_leak_is_reported(tmp_path: Path) -> None:
    diff = _write_diff(
        tmp_path,
        """diff --git a/worker.py b/worker.py
--- a/worker.py
+++ b/worker.py
@@ -0,0 +1 @@
+asyncio.create_task(process_message(message))
""",
    )
    findings = _run_skill(diff)
    assert any(item["title"] == "Untracked asynchronous task" for item in findings)


def test_case_database_connection_lifecycle_is_reported(tmp_path: Path) -> None:
    diff = _write_diff(
        tmp_path,
        """diff --git a/store.py b/store.py
--- a/store.py
+++ b/store.py
@@ -0,0 +1 @@
+connection = sqlite3.connect(database_path)
""",
    )
    findings = _run_skill(diff)
    assert any(
        item["category"] == "reliability" and item["title"] == "Resource lifecycle needs verification"
        for item in findings
    )


def test_case_missing_tests_is_reported(tmp_path: Path) -> None:
    diff = _write_diff(
        tmp_path,
        """diff --git a/discount.py b/discount.py
--- a/discount.py
+++ b/discount.py
@@ -0,0 +1,2 @@
+def apply_discount(amount):
+    return amount * 0.8
""",
    )
    findings = _run_skill(diff)
    assert any(item["category"] == "testing" and item["title"] == "No related test change" for item in findings)


def test_case_duplicate_findings_are_collapsed(tmp_path: Path) -> None:
    parsed = parse_review_input(diff_file=_diff(tmp_path))
    candidates = [
        Finding(
            severity="high",
            category="security",
            file="app.py",
            line=2,
            title="Unsafe shell",
            evidence="shell=True",
            recommendation="Disable shell",
            confidence=confidence,
            source=source,
        )
        for confidence, source in ((0.72, "rule"), (0.96, "model"))
    ]
    findings, warnings, human = normalize_findings(candidates, parsed)
    assert not warnings and not human
    assert len(findings) == 1
    assert findings[0].confidence == 0.96
    assert findings[0].source == "model"


@pytest.mark.asyncio
async def test_case_sandbox_failure_produces_partial_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = parse_review_input(diff_file=_diff(tmp_path))
    failed_run = SandboxRunResult(
        id="sandbox-failed",
        runtime="container",
        command=["python3", "runner.py"],
        status="failed",
        exit_code=None,
        stderr_summary="container initialization failed",
        decision=FilterDecision(
            decision=Decision.ALLOW,
            reason_code="allowed",
            reason="Execution allowed",
        ),
    )

    async def fake_sandbox_checks(
        *args: object,
        **kwargs: object,
    ) -> tuple[list[SandboxRunResult], list[Finding], list[str]]:
        return [failed_run], [], ["rule check failed: SandboxError"]

    monkeypatch.setattr(
        "examples.skills_code_review_agent.agent.review.run_sandbox_checks",
        fake_sandbox_checks,
    )
    report = await run_review(ReviewRequest(review_input=parsed, runtime="container"))
    assert report.status == "partial"
    assert report.findings == []
    assert report.sandbox_runs[0].status == "failed"
    assert "SandboxError" in report.warnings[0]


def test_case_sensitive_information_is_redacted_everywhere(tmp_path: Path) -> None:
    api_key = "sk-prod-1234567890abcdef"
    database_url = "postgres://admin:SuperSecret123@db.example.com/prod"
    text = f"api_key={api_key} database={database_url}"
    redacted = redact_sensitive_text(text)
    assert api_key not in redacted
    assert "SuperSecret123" not in redacted
    assert "[REDACTED_CREDENTIAL]" in redacted
    assert "[REDACTED_PASSWORD]" in redacted

    parsed = parse_review_input(diff_file=_diff(tmp_path))
    finding = Finding(
        severity="critical",
        category="security",
        file="app.py",
        line=2,
        title=text,
        evidence=text,
        recommendation=text,
        confidence=0.99,
    )
    findings, _, _ = normalize_findings([finding], parsed)
    serialized = findings[0].model_dump_json()
    assert api_key not in serialized
    assert "SuperSecret123" not in serialized


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-1234567890abcdefghijklmnop",
        "ghp_1234567890abcdefghijklmnop",
        "github_pat_1234567890abcdefghijklmnop",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "AKIA1234567890ABCDEF",
        "token='value with spaces'",
        "password: SuperSecret123",
        "postgresql://user:password@localhost/db",
        "eyJabcdefghij.abcdefghijkl.abcdefghijkl",
    ],
)
def test_common_secret_formats_are_redacted(secret: str) -> None:
    assert secret not in redact_sensitive_text(secret)


@pytest.mark.asyncio
async def test_needs_human_review_never_reaches_executor(tmp_path: Path) -> None:
    runner = SandboxRunner.__new__(SandboxRunner)
    runner.executor = object()
    runner.budget = ExecutionBudget(max_total_seconds=1000)
    task = SandboxTask(
        id="high-resource",
        task_type="test",
        command=["pytest"],
        cwd=str(tmp_path),
        resources=ResourcePolicy(timeout_seconds=301, memory_mb=1024),
    )
    result = await runner.run(object(), task)
    assert result.status == "blocked"
    assert result.decision.decision == Decision.NEEDS_HUMAN_REVIEW


@pytest.mark.parametrize(
    "fixture_name",
    [
        "clean.diff",
        "security.diff",
        "async_leak.diff",
        "database_leak.diff",
        "missing_tests.diff",
        "duplicate.diff",
        "sensitive.diff",
        "sandbox_failure.diff",
    ],
)
@pytest.mark.asyncio
async def test_public_fixtures_generate_reports(
    tmp_path: Path,
    fixture_name: str,
) -> None:
    fixture = (
        Path(__file__).parents[2]
        / "examples"
        / "skills_code_review_agent"
        / "fixtures"
        / fixture_name
    )
    parsed = parse_review_input(fixture_path=fixture)
    report = await run_review(
        ReviewRequest(review_input=parsed, runtime="local", fake_model=True)
    )
    paths = write_reports(report, tmp_path / "reports")
    assert paths.json_path.is_file()
    assert paths.markdown_path.is_file()
    assert report.status in {"completed", "partial"}
