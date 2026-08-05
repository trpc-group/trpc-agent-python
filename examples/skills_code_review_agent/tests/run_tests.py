#!/usr/bin/env python3
"""Run deterministic acceptance tests without a model API or Docker daemon."""

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXAMPLE_ROOT))

from filters.policy import CommandPolicy
from filters.policy import ReviewPolicyContext
from filters.policy import SandboxCommand
from filters.sdk_filter import FILTER_DECISIONS_METADATA_KEY
from filters.sdk_filter import SandboxToolFilter
from agent.config import ModelConfig
from agent.config import ReviewLimits
from agent.tools import SAFE_SKILL_TOOLS
from agent.tools import create_skill_tools
from agent.fake import analyze_with_fake_model
from agent.normalization import normalize_analysis
from agent.normalization import enforce_analysis_scope
from agent.prompts import build_review_request
from inputs.parser import _diff_parser_module
from inputs.parser import parse_diff_text
from inputs.parser import parse_diff_file
from inputs.parser import parse_git_worktree
from inputs.parser import cleanup_parsed_input
from reports.models import ReviewAnalysis
from reports.models import ReviewFinding
from reports.models import ReviewReport
from reports.models import ReviewScope
from reports.models import SandboxRun
from reports.writers import ReportWriter
from run_agent import load_env_file
from run_agent import find_git_worktree
from security import redact_text
from security import is_likely_secret_path
from sandbox.docker import DockerSandbox
from sandbox.docker import _BOUNDED_RUN_SCRIPT
from sandbox.docker import _BoundedProgramRunner
from sandbox.docker import _HardenedContainerClient
from sandbox.factory import create_sandbox_provider
from storage.factory import create_review_store
from storage.postgresql import PostgreSQLReviewStore
from storage.postgresql import validate_postgres_dsn
from storage.sqlite import SQLiteReviewStore
from workflow import AgentExecutionFailure
from workflow import CodeReviewWorkflow
from workflow import ReviewRequest
from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.tools import SetModelResponseTool


class FakeWorkflowTests(unittest.TestCase):
    """Cover all public fixtures through the complete fake workflow."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = SQLiteReviewStore(root / "reviews.sqlite3")
        self.workflow = CodeReviewWorkflow(
            model_config=None,
            sandbox=None,
            store=self.store,
            report_writer=ReportWriter(root / "reports"),
            skills_path=EXAMPLE_ROOT / "skills",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_fixture(self, name: str):
        return asyncio.run(
            self.workflow.run(
                ReviewRequest(
                    fixture=name,
                    scope=ReviewScope.CHANGED,
                    fake_model=True,
                )
            )
        )

    @staticmethod
    def load_skill_script(name: str):
        path = EXAMPLE_ROOT / "skills" / "code-review" / "scripts" / name
        spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"unable to load {path}")
        module = importlib.util.module_from_spec(spec)
        script_directory = str(path.parent)
        sys.path.insert(0, script_directory)
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(script_directory)
        return module

    def test_clean_diff(self) -> None:
        result = self.run_fixture("clean")
        self.assertEqual(result.report.analysis.findings, [])
        self.assertTrue(result.artifacts.json_path.is_file())
        self.assertTrue(result.artifacts.markdown_path.is_file())
        self.assertEqual(result.artifacts.json_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result.artifacts.json_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.database_path.stat().st_mode & 0o777, 0o600)

    def test_security_issue(self) -> None:
        result = self.run_fixture("security")
        self.assertIn("security", {item.category for item in result.report.analysis.findings})

    def test_async_resource_leak(self) -> None:
        result = self.run_fixture("async-resource-leak")
        self.assertIn("async_error", {item.category for item in result.report.analysis.findings})

    def test_database_connection_lifecycle(self) -> None:
        result = self.run_fixture("database-lifecycle")
        categories = {item.category for item in result.report.analysis.findings}
        self.assertIn("database_lifecycle", categories)

    def test_missing_test_is_warning(self) -> None:
        result = self.run_fixture("test-missing")
        self.assertNotIn(
            "test_missing",
            {item.category for item in result.report.analysis.findings},
        )
        self.assertIn(
            "test_missing",
            {item.category for item in result.report.analysis.warnings},
        )

    def test_duplicate_finding_is_deduplicated(self) -> None:
        result = self.run_fixture("duplicate-finding")
        security = [
            item for item in result.report.analysis.findings if item.category == "security"
        ]
        self.assertEqual(len(security), 1)

    def test_duplicate_finding_is_deduplicated_across_buckets(self) -> None:
        def finding(confidence: float) -> ReviewFinding:
            return ReviewFinding(
                severity="high",
                category="security",
                file="app.py",
                line=10,
                title="Duplicate",
                evidence="same evidence",
                recommendation="fix it",
                confidence=confidence,
                source="test",
            )

        normalized = normalize_analysis(
            ReviewAnalysis(
                summary="duplicate buckets",
                findings=[finding(0.80)],
                warnings=[finding(0.85)],
                needs_human_review=[finding(0.90)],
            )
        )
        self.assertEqual(normalized.findings, [])
        self.assertEqual(normalized.warnings, [])
        self.assertEqual(len(normalized.needs_human_review), 1)

        low_confidence = normalize_analysis(
            ReviewAnalysis(
                summary="low confidence",
                findings=[
                    finding(0.60).model_copy(
                        update={"category": "resource_leak", "line": 20}
                    )
                ],
            )
        )
        self.assertEqual(low_confidence.findings, [])
        self.assertEqual(len(low_confidence.warnings), 1)

    def test_unknown_model_line_sentinels_normalize_to_null(self) -> None:
        finding = ReviewFinding(
            severity="medium",
            category="test",
            file="app.py",
            line=-1,
            title="Unknown line",
            evidence="No precise line was available.",
            recommendation="Review the file.",
            confidence=0.8,
            source="test",
        )
        self.assertIsNone(finding.line)

    def test_model_findings_must_match_selected_diff_evidence(self) -> None:
        parsed = parse_diff_text(
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            kind="diff_file",
            source="change.diff",
            input_root=Path(self.temp_dir.name),
        )

        def finding(file: str, line: int) -> ReviewFinding:
            return ReviewFinding(
                severity="high",
                category="correctness",
                file=file,
                line=line,
                title="Issue",
                evidence="Evidence",
                recommendation="Fix it",
                confidence=0.9,
                source="model",
            )

        scoped = enforce_analysis_scope(
            ReviewAnalysis(
                summary="scope validation",
                findings=[
                    finding("app.py", 1),
                    finding("app.py", 99),
                    finding("unrelated.py", 1),
                ],
            ),
            parsed,
        )
        self.assertEqual([(item.file, item.line) for item in scoped.findings], [("app.py", 1)])
        self.assertEqual(
            scoped.needs_human_review[0].category,
            "agent_evidence_validation",
        )

    def test_sandbox_failure_does_not_abort_report(self) -> None:
        result = self.run_fixture("sandbox-failure")
        self.assertEqual(result.report.sandbox_runs[0].status, "failed")
        self.assertEqual(result.report.status, "completed_with_warnings")
        self.assertTrue(result.report.analysis.needs_human_review)

    def test_sandbox_timeout_does_not_abort_report(self) -> None:
        result = self.run_fixture("sandbox-timeout")
        run = result.report.sandbox_runs[0]
        self.assertEqual(run.status, "timeout")
        self.assertTrue(run.timed_out)
        self.assertEqual(result.report.status, "completed_with_warnings")
        self.assertEqual(
            result.report.monitoring.exception_distribution,
            {"TimeoutError": 1},
        )
        self.assertIn(
            "review_execution_limitation",
            {
                item.category
                for item in result.report.analysis.needs_human_review
            },
        )

    def test_unexpected_real_execution_failure_is_persisted(self) -> None:
        result = asyncio.run(
            self.workflow.run(ReviewRequest(fixture="clean"))
        )
        self.assertEqual(result.report.status, "failed")
        self.assertEqual(result.report.sandbox_runs[0].status, "failed")
        details = self.store.get_task_details(result.report.task_id)
        self.assertEqual(details["task"]["status"], "failed")

    def test_partial_agent_audit_survives_structured_output_failure(self) -> None:
        command = "git -C /etc status"
        decision = CommandPolicy().evaluate(SandboxCommand(command=command))
        failure = AgentExecutionFailure(
            ValueError("structured output failed"),
            [decision],
            [],
            3,
        )
        with patch.object(
            self.workflow,
            "_run_agent",
            new=AsyncMock(side_effect=failure),
        ):
            result = asyncio.run(
                self.workflow.run(ReviewRequest(fixture="clean"))
            )
        self.assertEqual(result.report.status, "failed")
        self.assertEqual(result.report.monitoring.tool_call_count, 3)
        self.assertEqual(result.report.monitoring.blocked_count, 1)
        self.assertEqual(result.report.filter_decisions[0].decision, "deny")
        self.assertIn("blocked", {run.status for run in result.report.sandbox_runs})

    def test_sensitive_values_are_redacted_everywhere(self) -> None:
        result = self.run_fixture("sensitive-redaction")
        report_text = result.artifacts.json_path.read_text(encoding="utf-8")
        database_bytes = self.store.database_path.read_bytes()
        for secret in (
            "sk-testabcdefghijklmnop",
            "not-a-real-password",
            "dummy-token-value",
            "abcdefghijklmnop",
            "AKIAABCDEFGHIJKLMNOP",
            "ghp_abcdefghijklmnopqrstuvwxyz",
            "eyJheader.payload.signaturevalue",
            "dummy-password",
            "ABCDEF0123456789",
            "plain-aws-secret-material",
            "plain-json-api-key",
        ):
            self.assertNotIn(secret, report_text)
            self.assertNotIn(secret.encode(), database_bytes)
        loaded = self.store.get(result.report.task_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.task_id, result.report.task_id)

    def test_input_preview_is_redacted_before_truncation(self) -> None:
        private_key = (
            "-----BEGIN PRIVATE KEY-----"
            + "A" * 2100
            + "-----END PRIVATE KEY-----"
        )
        parsed = parse_diff_text(
            "--- a/key.py\n+++ b/key.py\n@@ -0,0 +1 @@\n+" + private_key + "\n",
            kind="diff_file",
            source="key.diff",
            input_root=Path(self.temp_dir.name),
        )
        self.assertNotIn("A" * 20, parsed.summary.redacted_preview)
        self.assertIn("REDACTED_PRIVATE_KEY", parsed.summary.redacted_preview)

    def test_database_exposes_normalized_task_details(self) -> None:
        result = self.run_fixture("security")
        details = self.store.get_task_details(result.report.task_id)
        self.assertIsNotNone(details)
        self.assertTrue(details["sandbox_runs"])
        self.assertTrue(details["filter_decisions"])
        self.assertTrue(details["findings"])
        self.assertIsNotNone(details["monitoring"])
        self.assertIsNotNone(details["report"])

    def test_explicit_diff_file_input(self) -> None:
        root = Path(self.temp_dir.name)
        diff_path = root / "change.patch"
        diff_path.write_text(
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n"
            "-return value\n+return os.system(value)\n",
            encoding="utf-8",
        )
        result = asyncio.run(
            self.workflow.run(ReviewRequest(diff_file=diff_path, fake_model=True))
        )
        self.assertEqual(result.report.input_summary.kind, "diff_file")
        self.assertEqual(result.report.input_summary.source, "change.patch")
        self.assertTrue(result.report.analysis.findings)

    def test_exact_diff_history_is_available_to_agent(self) -> None:
        result = self.run_fixture("security")
        cached = self.store.get_latest_by_input_digest(
            result.report.input_summary.digest,
            result.report.input_summary.review_profile,
        )
        self.assertIsNotNone(cached)
        prompt = build_review_request(
            ReviewScope.CHANGED,
            result.report.input_summary,
            cached,
        )
        self.assertIn(f"Prior task: {result.report.task_id}", prompt)
        parsed = self.workflow._parse_input(
            ReviewRequest(fixture="security", fake_model=True)
        )
        parsed.summary.review_profile = result.report.input_summary.review_profile
        try:
            self.assertEqual(
                self.workflow._find_cached_report(parsed).task_id,
                result.report.task_id,
            )
        finally:
            cleanup_parsed_input(parsed)

    def test_cache_requires_matching_review_profile(self) -> None:
        result = self.run_fixture("security")
        self.assertIsNone(
            self.store.get_latest_by_input_digest(
                result.report.input_summary.digest,
                "different-profile",
            )
        )

    def test_diff_input_stages_only_selected_file(self) -> None:
        root = Path(self.temp_dir.name) / "private-parent"
        root.mkdir()
        diff_path = root / "change.diff"
        diff_path.write_text(
            "--- a/app.py\n+++ b/app.py\n@@ -0,0 +1 @@\n+value = 1\n",
            encoding="utf-8",
        )
        (root / ".env").write_text("API_KEY=must-not-be-mounted\n", encoding="utf-8")
        parsed = parse_diff_file(diff_path)
        staged_root = parsed.input_root
        try:
            self.assertEqual(
                [item.name for item in staged_root.iterdir()],
                ["change.diff"],
            )
            self.assertNotEqual(staged_root, root)
            self.assertEqual(staged_root.stat().st_mode & 0o777, 0o500)
            self.assertEqual(
                (staged_root / "change.diff").stat().st_mode & 0o777,
                0o400,
            )
        finally:
            cleanup_parsed_input(parsed)
        self.assertFalse(staged_root.exists())

        fixture = self.workflow._parse_input(
            ReviewRequest(fixture="security", fake_model=True)
        )
        try:
            self.assertTrue((fixture.input_root / "security.diff").is_file())
        finally:
            cleanup_parsed_input(fixture)

    def test_fixture_prompt_requires_load_before_exact_skill_command(self) -> None:
        parsed = self.workflow._parse_input(
            ReviewRequest(fixture="security", fake_model=True)
        )
        prompt = build_review_request(
            ReviewScope.CHANGED,
            parsed.summary,
        )
        self.assertIn(
            "python3 scripts/run_review_rules.py work/inputs/security.diff",
            prompt,
        )
        self.assertIn("Otherwise call `skill_load`", prompt)
        self.assertIn("Only then", prompt)

    def test_worktree_prompt_does_not_disclose_host_path(self) -> None:
        root = Path(self.temp_dir.name) / "private" / "repository"
        (root / ".git").mkdir(parents=True)
        parsed = parse_git_worktree(root)
        prompt = build_review_request(ReviewScope.CHANGED, parsed.summary)
        self.assertNotIn(str(root), prompt)
        self.assertIn("Input source: work/inputs", prompt)

    def test_incomplete_pagination_requires_human_review(self) -> None:
        parsed = self.workflow._parse_input(
            ReviewRequest(fixture="security", fake_model=True)
        )
        command = (
            "python3 scripts/run_review_rules.py "
            "work/inputs/security.diff"
        )
        self.workflow._update_runtime_input(
            parsed,
            command,
            {"stdout": json.dumps({"cursor": 0, "next_cursor": 24})},
        )
        run = SandboxRun(
            run_id="pagination-run",
            command=command,
            status="success",
        )
        limited = self.workflow._append_execution_limitations(
            ReviewAnalysis(summary="partial"),
            parsed,
            [],
            [run],
            require_complete_execution=True,
        )
        self.assertIn(
            "pagination did not finish",
            limited.needs_human_review[0].evidence,
        )
        self.workflow._update_runtime_input(
            parsed,
            f"{command} --cursor 24 --limit 24",
            {"stdout": json.dumps({"cursor": 24, "next_cursor": None})},
        )
        self.assertEqual(
            self.workflow._execution_completeness_issues(parsed, [run]),
            [],
        )

    def test_worktree_diff_evidence_is_merged(self) -> None:
        root = Path(self.temp_dir.name) / "repository"
        (root / ".git").mkdir(parents=True)
        parsed = parse_git_worktree(root)
        unstaged = (
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
        )
        staged = (
            "--- a/db.py\n+++ b/db.py\n@@ -1 +1 @@\n-old\n+new\n"
        )
        runner = self.load_skill_script("run_review_rules.py")
        parser = _diff_parser_module()

        def page(diff: str, mode: str, digest: str | None = None) -> str:
            payload = runner.build_page(parser.parse_unified_diff(diff))
            payload["mode"] = mode
            payload["input_digest"] = digest or f"{mode}-digest"
            return json.dumps(payload)

        self.workflow._update_runtime_input(
            parsed,
            "python3 scripts/inspect_git_files.py work/inputs --mode changed",
            {
                "stdout": json.dumps(
                    {
                        "mode": "changed",
                        "cursor": 0,
                        "next_cursor": None,
                        "total_files": 2,
                        "records": [
                            {"status": " M", "path": "app.py", "truncated": False},
                            {
                                "status": "??",
                                "path": "untracked.py",
                                "truncated": False,
                            },
                        ],
                    }
                )
            },
        )
        self.workflow._update_runtime_input(
            parsed,
            "python3 scripts/review_git_changes.py work/inputs --mode unstaged",
            {"stdout": page(unstaged, "unstaged")},
        )
        self.workflow._update_runtime_input(
            parsed,
            "python3 scripts/review_git_changes.py work/inputs --mode staged",
            {"stdout": page(staged, "staged")},
        )
        # Retrying the first page must not double-count summary metrics.
        self.workflow._update_runtime_input(
            parsed,
            "python3 scripts/review_git_changes.py work/inputs --mode unstaged",
            {"stdout": page(unstaged, "unstaged")},
        )
        self.assertEqual(
            parsed.summary.files,
            ["app.py", "untracked.py", "db.py"],
        )
        self.assertEqual(parsed.summary.file_count, 3)
        self.assertEqual(parsed.summary.hunk_count, 2)
        self.assertNotEqual(parsed.summary.digest, "pending-sandbox-diff")
        self.workflow._update_runtime_input(
            parsed,
            "python3 scripts/review_git_changes.py work/inputs --mode unstaged",
            {"stdout": page(unstaged, "unstaged", "changed-digest")},
        )
        self.assertTrue(parsed.input_changed_during_review)
        limited = self.workflow._append_execution_limitations(
            ReviewAnalysis(summary="changed input"),
            parsed,
            [],
            [],
        )
        self.assertEqual(
            limited.needs_human_review[0].category,
            "review_execution_limitation",
        )

    def test_file_list_input(self) -> None:
        root = Path(self.temp_dir.name)
        list_path = root / "files.txt"
        list_path.write_text("app.py\ntests/test_app.py\n", encoding="utf-8")
        result = asyncio.run(
            self.workflow.run(ReviewRequest(file_list=list_path, fake_model=True))
        )
        self.assertEqual(result.report.input_summary.kind, "file_list")
        self.assertEqual(result.report.input_summary.file_count, 2)

        list_path.write_text("app.py\n.env\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "likely secret"):
            asyncio.run(
                self.workflow.run(
                    ReviewRequest(file_list=list_path, fake_model=True)
                )
            )

    def test_full_scope_requires_repository_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "Full review requires"):
            asyncio.run(
                self.workflow.run(
                    ReviewRequest(
                        fixture="clean",
                        scope=ReviewScope.FULL,
                        fake_model=True,
                    )
                )
            )

    def test_filter_blocks_risk_network_path_and_budget(self) -> None:
        policy = CommandPolicy()
        cases = (
            SandboxCommand(command="rm -rf work", timeout_seconds=10),
            SandboxCommand(command="git status", network_required=True),
            SandboxCommand(command="git -C /etc status"),
            SandboxCommand(command="git status", timeout_seconds=121),
            SandboxCommand(command="git status", max_output_bytes=1024 * 1024 + 1),
            SandboxCommand(command="git status", environment={"API_KEY": "dummy"}),
            SandboxCommand(command="git status", environment={"PATH": "work/inputs"}),
            SandboxCommand(command="git status", environment={"LANG": "C; injected"}),
            SandboxCommand(command="python3 /tmp/unsafe.py"),
            SandboxCommand(command="python3 scripts/../unsafe.py"),
        )
        decisions = [policy.evaluate(item).decision for item in cases]
        self.assertEqual(decisions[0], "needs_human_review")
        self.assertTrue(all(item != "allow" for item in decisions))
        self.assertEqual(
            policy.evaluate(
                SandboxCommand(command="git status", environment={"LANG": "C.UTF-8"})
            ).decision,
            "allow",
        )

    def test_environment_cannot_raise_hard_sandbox_budgets(self) -> None:
        with patch.dict(
            os.environ,
            {"CODE_REVIEW_MAX_OUTPUT_BYTES": str(1024 * 1024 + 1)},
        ):
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                CommandPolicy.from_env()
        with patch.dict(
            os.environ,
            {"CODE_REVIEW_TOTAL_TIMEOUT_SECONDS": "121"},
        ):
            with self.assertRaisesRegex(ValueError, "120"):
                ReviewLimits.from_env()
        with patch.dict(
            os.environ,
            {"CODE_REVIEW_MAX_SANDBOX_RUNS": "13"},
        ):
            with self.assertRaisesRegex(ValueError, "12"):
                SandboxToolFilter()
        with patch.dict(
            os.environ,
            {"CODE_REVIEW_DOCKER_PIDS_LIMIT": "257"},
        ):
            with self.assertRaisesRegex(ValueError, "256"):
                create_sandbox_provider()
        with patch.dict(
            os.environ,
            {"CODE_REVIEW_ALLOW_REPOSITORY_EXECUTION": "maybe"},
        ):
            with self.assertRaisesRegex(ValueError, "must be true or false"):
                CommandPolicy.from_env()

    def test_filter_blocks_composed_and_unapproved_scripts(self) -> None:
        policy = CommandPolicy()
        cases = (
            SandboxCommand(command="git status && rm -rf work"),
            SandboxCommand(command="git status > out/status.txt"),
            SandboxCommand(
                command="python3 scripts/parse_unified_diff.py $HOME/input.diff"
            ),
            SandboxCommand(command="python3 scripts/unknown.py"),
            SandboxCommand(command="git push origin main"),
        )
        self.assertTrue(
            all(policy.evaluate(item).decision == "needs_human_review" for item in cases)
        )

    def test_repository_execution_requires_explicit_opt_in(self) -> None:
        for command in (
            "python3 -m unittest discover -s work/inputs/tests",
            "pytest work/inputs/tests",
        ):
            self.assertEqual(
                CommandPolicy().evaluate(SandboxCommand(command=command)).decision,
                "needs_human_review",
            )
            self.assertEqual(
                CommandPolicy(allow_repository_execution=True)
                .evaluate(SandboxCommand(command=command))
                .decision,
                "allow",
            )
        self.assertEqual(
            CommandPolicy()
            .evaluate(
                SandboxCommand(command="python3 -m compileall work/inputs/app.py")
            )
            .decision,
            "allow",
        )

    def test_context_filter_restricts_diff_to_paginated_runner(self) -> None:
        context = ReviewPolicyContext(
            input_kind="diff_file",
            source="change.diff",
            scope="changed",
        )
        policy = CommandPolicy(context=context)
        allowed = (
            "python3 scripts/run_review_rules.py work/inputs/change.diff "
            "--cursor 24 --limit 24"
        )
        self.assertEqual(
            policy.evaluate(SandboxCommand(command=allowed)).decision,
            "allow",
        )
        for command in (
            "python3 scripts/inspect_files.py work/inputs --path .env",
            "python3 scripts/review_security.py work/inputs/change.diff",
            "git -C work/inputs status --short",
            "python3 scripts/run_review_rules.py work/inputs/other.diff",
        ):
            self.assertEqual(
                policy.evaluate(SandboxCommand(command=command)).decision,
                "deny",
            )

    def test_context_filter_blocks_git_helpers_and_secret_paths(self) -> None:
        policy = CommandPolicy(
            context=ReviewPolicyContext(
                input_kind="git_worktree",
                source="repository",
                scope="changed",
            )
        )
        self.assertEqual(
            policy.evaluate(
                SandboxCommand(
                    command=(
                        "python3 scripts/inspect_git_files.py work/inputs "
                        "--mode changed --limit 12"
                    )
                )
            ).decision,
            "allow",
        )
        self.assertEqual(
            policy.evaluate(
                SandboxCommand(
                    command=(
                        "python3 scripts/inspect_files.py work/inputs "
                        "--scope changed --path app.py"
                    )
                )
            ).decision,
            "allow",
        )
        self.assertEqual(
            policy.evaluate(
                SandboxCommand(
                    command=(
                        "python3 scripts/inspect_files.py work/inputs "
                        "--scope full --path app.py"
                    )
                )
            ).decision,
            "deny",
        )
        self.assertEqual(
            policy.evaluate(
                SandboxCommand(
                    command=(
                        "python3 scripts/review_git_changes.py work/inputs "
                        "--mode unstaged --cursor 24 --limit 24"
                    )
                )
            ).decision,
            "allow",
        )
        for command in (
            "git -C work/inputs diff --ext-diff",
            "git -C work/inputs diff --textconv",
            "git -C work/inputs diff --no-ext-diff --no-textconv",
            "git -C work/inputs diff --output=work/inputs/result.diff",
            "git -C work/inputs status --short --untracked-files=no",
            "git -C work/inputs ls-files",
            "python3 scripts/inspect_files.py work/inputs --path .env",
            "python3 scripts/inspect_files.py work/inputs --path secrets/token.pem",
            (
                "python3 scripts/review_git_changes.py work/inputs "
                "--mode all"
            ),
        ):
            self.assertEqual(
                policy.evaluate(SandboxCommand(command=command)).decision,
                "deny",
            )

        full_policy = CommandPolicy(
            context=ReviewPolicyContext(
                input_kind="git_worktree",
                source="repository",
                scope="full",
            )
        )
        self.assertEqual(
            full_policy.evaluate(
                SandboxCommand(
                    command=(
                        "python3 scripts/inspect_git_files.py work/inputs "
                        "--mode tracked"
                    )
                )
            ).decision,
            "allow",
        )
        self.assertEqual(
            full_policy.evaluate(
                SandboxCommand(command="git -C work/inputs status --short")
            ).decision,
            "deny",
        )

    def test_secret_path_detection_does_not_hide_normal_source_files(self) -> None:
        for path in (
            "src/tokenizer.py",
            "src/password_validator.py",
            "src/api_token.ts",
        ):
            self.assertFalse(is_likely_secret_path(path), path)
        for path in (
            ".env.local",
            "config/credentials.json",
            "secrets-prod/config.json",
            "keys/private.pem",
        ):
            self.assertTrue(is_likely_secret_path(path), path)

    def test_filter_enforces_review_sandbox_run_budget(self) -> None:
        context = new_agent_context()
        filter_instance = SandboxToolFilter(max_sandbox_runs=1)
        first = FilterResult()
        second = FilterResult()
        asyncio.run(
            filter_instance._before(
                context,
                {"skill": "code-review", "command": "git status"},
                first,
            )
        )
        asyncio.run(
            filter_instance._before(
                context,
                {"skill": "code-review", "command": "git status"},
                second,
            )
        )
        self.assertTrue(first.is_continue)
        self.assertFalse(second.is_continue)
        decisions = context.get_metadata(FILTER_DECISIONS_METADATA_KEY)
        self.assertEqual(decisions[-1]["decision"], "deny")
        self.assertIn("budget", decisions[-1]["reason"])

    def test_sdk_filter_stops_denied_tool_call(self) -> None:
        context = new_agent_context()
        response = FilterResult()
        asyncio.run(
            SandboxToolFilter()._before(
                context,
                {"skill": "code-review", "command": "git -C /etc status"},
                response,
            )
        )
        self.assertFalse(response.is_continue)
        self.assertIsInstance(response.error, PermissionError)
        decisions = context.get_metadata(FILTER_DECISIONS_METADATA_KEY)
        self.assertEqual(decisions[0]["decision"], "deny")

    def test_sdk_filter_records_malformed_request_as_denied(self) -> None:
        context = new_agent_context()
        response = FilterResult()
        asyncio.run(
            SandboxToolFilter()._before(
                context,
                {
                    "skill": "code-review",
                    "command": "git status",
                    "timeout": "not-a-number",
                },
                response,
            )
        )
        self.assertFalse(response.is_continue)
        self.assertIsInstance(response.error, PermissionError)
        decisions = context.get_metadata(FILTER_DECISIONS_METADATA_KEY)
        self.assertEqual(decisions[0]["decision"], "deny")
        self.assertIn("invalid", decisions[0]["reason"])

    def test_sdk_filter_rejects_skill_and_staging_parameter_bypasses(self) -> None:
        requests = (
            {"skill": "other", "command": "git status"},
            {
                "skill": "code-review",
                "command": "git status",
                "stdin": "untrusted input",
            },
            {
                "skill": "code-review",
                "command": "git status",
                "output_files": ["../../secret"],
            },
            {
                "skill": "code-review",
                "command": "git status",
                "unknown_option": True,
            },
        )
        context = new_agent_context()
        filter_instance = SandboxToolFilter(max_sandbox_runs=len(requests))
        for request in requests:
            response = FilterResult()
            asyncio.run(filter_instance._before(context, request, response))
            self.assertFalse(response.is_continue)
            self.assertIsInstance(response.error, PermissionError)

    def test_filter_error_response_is_a_blocked_audit_run(self) -> None:
        command = "git -C /etc status"
        run = self.workflow._sandbox_run_from_response(
            (command, time.perf_counter()),
            {
                "error": "PermissionError",
                "message": "command references a forbidden path",
                "status": "failed",
            },
        )
        decision = CommandPolicy().evaluate(SandboxCommand(command=command))
        normalized = self.workflow._apply_filter_decisions([run], [decision])[0]
        self.assertEqual(normalized.status, "blocked")
        self.assertEqual(normalized.error_type, "FilterBlocked")
        self.assertIn("forbidden path", normalized.stderr_summary)

    def test_sandbox_output_is_redacted_before_report_summary_clipping(self) -> None:
        private_key = "-----BEGIN PRIVATE KEY-----\n" + "A" * 2100
        run = self.workflow._sandbox_run_from_response(
            ("python3 scripts/inspect_files.py", time.perf_counter()),
            {"stdout": private_key, "exit_code": 0},
        )
        self.assertFalse(run.output_truncated)
        self.assertNotIn("A" * 20, run.stdout_summary)
        self.assertIn("REDACTED_PRIVATE_KEY", run.stdout_summary)

        truncated = self.workflow._sandbox_run_from_response(
            ("python3 scripts/inspect_files.py", time.perf_counter()),
            {
                "stdout": "partial\n[output truncated by sandbox policy]",
                "exit_code": 0,
            },
        )
        self.assertTrue(truncated.output_truncated)

    def test_skill_diff_parser_redacts_by_default(self) -> None:
        parsed = _diff_parser_module().parse_unified_diff(
            "--- a/settings.py\n+++ b/settings.py\n@@ -0,0 +1 @@\n"
            "+API_KEY = \"sk-testabcdefghijklmnop\"\n"
        )
        content = parsed["files"][0]["hunks"][0]["changes"][0]["content"]
        self.assertNotIn("sk-testabcdefghijklmnop", content)
        self.assertIn("REDACTED", content)

    def test_additional_service_token_formats_are_redacted(self) -> None:
        tokens = (
            "sk_live_abcdefghijklmnop",
            "xoxb-1234567890-abcdefghijklmnop",
            "AIzaABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            "ASIAABCDEFGHIJKLMNOP",
            "github_pat_abcdefghijklmnopqrstuvwxyz123456",
            "glpat-abcdefghijklmnopqrst",
            "npm_abcdefghijklmnopqrstuvwxyz",
            "pypi-abcdefghijklmnopqrstuvwxyz",
            "hf_abcdefghijklmnopqrstuvwxyz",
        )
        parser = _diff_parser_module()
        for token in tokens:
            self.assertNotIn(token, redact_text(token))
            parsed = parser.parse_unified_diff(
                "--- a/settings.py\n+++ b/settings.py\n@@ -0,0 +1 @@\n"
                f"+value = '{token}'\n"
            )
            content = parsed["files"][0]["hunks"][0]["changes"][0]["content"]
            self.assertNotIn(token, content)
            self.assertIn("REDACTED", content)

    def test_aggregate_rule_runner_covers_each_documented_category(self) -> None:
        runner = self.load_skill_script("run_review_rules.py")
        parser = _diff_parser_module()
        fixture_categories = {}
        for fixture in (
            "security",
            "async-resource-leak",
            "database-lifecycle",
            "test-missing",
            "sensitive-redaction",
        ):
            path = EXAMPLE_ROOT / "tests" / "fixtures" / f"{fixture}.diff"
            parsed = parser.parse_unified_diff(path.read_text(encoding="utf-8"))
            fixture_categories[fixture] = {
                item["category"] for item in runner.run_all(parsed)
            }

        resource_diff = (
            "--- a/io.py\n+++ b/io.py\n@@ -0,0 +1 @@\n"
            "+handle = open(path)\n"
        )
        resource_categories = {
            item["category"]
            for item in runner.run_all(parser.parse_unified_diff(resource_diff))
        }
        self.assertIn("security", fixture_categories["security"])
        self.assertIn("async_error", fixture_categories["async-resource-leak"])
        self.assertIn("database_lifecycle", fixture_categories["database-lifecycle"])
        self.assertIn("test_missing", fixture_categories["test-missing"])
        self.assertIn(
            "sensitive_information",
            fixture_categories["sensitive-redaction"],
        )
        self.assertIn("resource_leak", resource_categories)

    def test_rule_scripts_are_individually_filter_allowlisted(self) -> None:
        policy = CommandPolicy()
        scripts = (
            "review_security.py",
            "inspect_git_files.py",
            "review_async.py",
            "review_resources.py",
            "review_database.py",
            "review_git_changes.py",
            "review_tests.py",
            "review_secrets.py",
            "run_review_rules.py",
        )
        for script in scripts:
            path = EXAMPLE_ROOT / "skills" / "code-review" / "scripts" / script
            self.assertTrue(path.is_file())
            decision = policy.evaluate(
                SandboxCommand(
                    command=f"python3 scripts/{script} work/inputs/change.diff"
                )
            )
            self.assertEqual(decision.decision, "allow", script)

    def test_git_diff_collector_uses_fixed_bounded_commands(self) -> None:
        module = self.load_skill_script("review_git_changes.py")
        repository = Path(self.temp_dir.name) / "git-repository"
        (repository / ".git").mkdir(parents=True)
        diff = b"--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=diff,
            stderr=b"",
        )
        with patch.object(module.subprocess, "run", return_value=completed) as run:
            self.assertEqual(module.collect_diff(repository, "staged"), diff.decode())
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["git", "-C", str(repository), "diff"])
        self.assertEqual(
            command[4:],
            ["--cached", "--no-ext-diff", "--no-textconv"],
        )
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_git_file_enumerator_handles_nul_paths_and_renames(self) -> None:
        module = self.load_skill_script("inspect_git_files.py")
        repository = Path(self.temp_dir.name) / "git-file-repository"
        (repository / ".git").mkdir(parents=True)
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                b" M app.py\0"
                b"R  renamed.py\0old.py\0"
                b"?? path with spaces.py\0"
                b"?? line\nbreak.py\0"
            ),
            stderr=b"",
        )
        with patch.object(module.subprocess, "run", return_value=completed) as run:
            records = module.collect_files(repository, "changed")
        self.assertEqual(
            [item["path"] for item in records],
            ["app.py", "renamed.py", "path with spaces.py", "line�break.py"],
        )
        self.assertTrue(records[-1]["normalized"])
        self.assertEqual(
            run.call_args.args[0],
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--short",
                "-z",
                "--untracked-files=all",
            ],
        )
        page = module.build_page(records, mode="changed", limit=2)
        self.assertEqual(page["next_cursor"], 2)

    def test_git_helpers_run_against_a_real_temporary_worktree(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        files_module = self.load_skill_script("inspect_git_files.py")
        diff_module = self.load_skill_script("review_git_changes.py")
        repository = Path(self.temp_dir.name) / "real-git-repository"
        repository.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", str(repository)],
            check=True,
            capture_output=True,
        )
        app = repository / "app.py"
        app.write_text("value = 'old'\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "app.py"],
            check=True,
            capture_output=True,
        )
        app.write_text("value = 'new'\n", encoding="utf-8")
        (repository / "new file.py").write_text("created = True\n", encoding="utf-8")

        records = files_module.collect_files(repository, "changed")
        self.assertEqual(
            {item["path"] for item in records},
            {"app.py", "new file.py"},
        )
        diff = diff_module.collect_diff(repository, "unstaged")
        self.assertIn("+value = 'new'", diff)

    def test_rule_runner_never_emits_plaintext_secrets(self) -> None:
        runner = self.load_skill_script("run_review_rules.py")
        parser = _diff_parser_module()
        plaintext = "sk-testabcdefghijklmnop"
        parsed = parser.parse_unified_diff(
            "--- a/settings.py\n+++ b/settings.py\n@@ -0,0 +1 @@\n"
            f'+API_KEY = "{plaintext}"\n'
        )
        output = str(runner.run_all(parsed))
        self.assertNotIn(plaintext, output)
        self.assertIn("sensitive_information", output)

    def test_diff_parser_handles_multiple_plain_unified_files(self) -> None:
        parsed = _diff_parser_module().parse_unified_diff(
            "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n--- old\n+++ new\n"
            "--- a/db.py\n+++ b/db.py\n@@ -1 +1 @@\n-old\n+new\n"
        )
        self.assertEqual(
            [item["new_path"] for item in parsed["files"]],
            ["app.py", "db.py"],
        )
        first_changes = parsed["files"][0]["hunks"][0]["changes"]
        self.assertEqual(
            [item["content"] for item in first_changes],
            ["-- old", "++ new"],
        )

    def test_diff_parser_keeps_deleted_file_in_input_summary(self) -> None:
        parsed = parse_diff_text(
            "--- a/obsolete.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
            kind="diff_file",
            source="delete.diff",
            input_root=Path(self.temp_dir.name),
        )
        self.assertEqual(parsed.summary.files, ["obsolete.py"])
        self.assertEqual(parsed.summary.file_count, 1)
        self.assertEqual(parsed.files[0]["status"], "deleted")

    def test_diff_parser_preserves_unchanged_context_and_line_numbers(self) -> None:
        parsed = _diff_parser_module().parse_unified_diff(
            "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@ def run\n"
            " def run():\n"
            "-    return old_value\n"
            "+    return new_value\n"
        )
        hunk = parsed["files"][0]["hunks"][0]
        context = hunk["changes"][0]
        self.assertEqual(
            context,
            {
                "kind": "context",
                "old_line": 1,
                "new_line": 1,
                "content": "def run():",
            },
        )
        self.assertEqual(hunk["candidate_lines"], [2])

    def test_unchanged_cleanup_context_suppresses_lifecycle_candidates(self) -> None:
        diff = (
            "--- a/async_worker.py\n+++ b/async_worker.py\n"
            "@@ -1,2 +1,2 @@ async def start\n"
            "-task = legacy_schedule(run())\n"
            "+task = asyncio.create_task(run())\n"
            " await task\n"
            "--- a/io.py\n+++ b/io.py\n@@ -1,2 +1,2 @@ def read\n"
            "-handle = legacy_open(path)\n"
            "+handle = open(path)\n"
            " handle.close()\n"
            "--- a/db.py\n+++ b/db.py\n@@ -1,2 +1,2 @@ def query\n"
            "-connection = legacy_connect(path)\n"
            "+connection = sqlite3.connect(path)\n"
            " connection.close()\n"
        )
        parsed_input = parse_diff_text(
            diff,
            kind="diff_file",
            source="managed.diff",
            input_root=Path(self.temp_dir.name),
        )
        fake_categories = {
            item.category for item in analyze_with_fake_model(parsed_input).findings
        }
        self.assertTrue(
            {"async_error", "resource_leak", "database_lifecycle"}.isdisjoint(
                fake_categories
            )
        )

        runner = self.load_skill_script("run_review_rules.py")
        parsed = _diff_parser_module().parse_unified_diff(diff)
        skill_categories = {item["category"] for item in runner.run_all(parsed)}
        self.assertTrue(
            {"async_error", "resource_leak", "database_lifecycle"}.isdisjoint(
                skill_categories
            )
        )

    def test_fake_rules_cover_risks_without_flagging_managed_lifecycles(self) -> None:
        safe = parse_diff_text(
            "--- a/worker.py\n+++ b/worker.py\n@@ -0,0 +1,6 @@\n"
            "+task = asyncio.create_task(run())\n+await task\n"
            "+handle = open(path)\n+handle.close()\n"
            "+connection = sqlite3.connect(path)\n+connection.close()\n",
            kind="diff_file",
            source="safe.diff",
            input_root=Path(self.temp_dir.name),
        )
        self.assertEqual(analyze_with_fake_model(safe).findings, [])

        risky = parse_diff_text(
            "--- a/worker.py\n+++ b/worker.py\n@@ -0,0 +1,4 @@\n"
            "+task = asyncio.ensure_future(run())\n"
            "+handle = open(path)\n"
            "+rows = db.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n"
            "+value = pickle.loads(payload)\n",
            kind="diff_file",
            source="risky.diff",
            input_root=Path(self.temp_dir.name),
        )
        categories = {
            finding.category for finding in analyze_with_fake_model(risky).findings
        }
        self.assertTrue({"async_error", "resource_leak", "security"} <= categories)

    def test_security_rules_distinguish_literal_and_dynamic_execution(self) -> None:
        safe_diff = (
            "--- a/commands.py\n+++ b/commands.py\n@@ -0,0 +1,6 @@\n"
            '+os.system("clear")\n'
            '+subprocess.run("echo ready", shell=True)\n'
            "+yaml.load(payload,\n"
            "+    Loader=yaml.SafeLoader)\n"
            '+cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))\n'
            '+exec.Command("sh", "-c", "echo ready")\n'
        )
        risky_diff = (
            "--- a/commands.py\n+++ b/commands.py\n@@ -0,0 +1,6 @@\n"
            "+os.system(user_input)\n"
            "+subprocess.run(command, shell=True)\n"
            "+yaml.load(payload)\n"
            '+cursor.execute("SELECT * FROM users WHERE id = %s" % user_id)\n'
            "+db.query(`SELECT * FROM users WHERE id = ${userId}`)\n"
            '+exec.Command("sh", "-c", command)\n'
        )
        runner = self.load_skill_script("run_review_rules.py")
        parser = _diff_parser_module()
        safe_skill_categories = {
            item["category"]
            for item in runner.run_all(parser.parse_unified_diff(safe_diff))
        }
        risky_skill_categories = {
            item["category"]
            for item in runner.run_all(parser.parse_unified_diff(risky_diff))
        }
        self.assertNotIn("security", safe_skill_categories)
        self.assertIn("security", risky_skill_categories)

        safe_input = parse_diff_text(
            safe_diff,
            kind="diff_file",
            source="safe-commands.diff",
            input_root=Path(self.temp_dir.name),
        )
        risky_input = parse_diff_text(
            risky_diff,
            kind="diff_file",
            source="risky-commands.diff",
            input_root=Path(self.temp_dir.name),
        )
        self.assertNotIn(
            "security",
            {item.category for item in analyze_with_fake_model(safe_input).findings},
        )
        self.assertIn(
            "security",
            {item.category for item in analyze_with_fake_model(risky_input).findings},
        )

    def test_extended_hidden_like_high_risk_rules(self) -> None:
        runner = self.load_skill_script("run_review_rules.py")
        parser = _diff_parser_module()
        diff = (
            "diff --git a/command.js b/command.js\n"
            "--- a/command.js\n+++ b/command.js\n"
            "@@ -0,0 +1 @@\n+child_process.exec(request.query.cmd)\n"
            "diff --git a/jobs.py b/jobs.py\n"
            "--- a/jobs.py\n+++ b/jobs.py\n"
            "@@ -1 +1,2 @@\n async def run():\n+    asyncio.sleep(1)\n"
            "diff --git a/store.py b/store.py\n"
            "--- a/store.py\n+++ b/store.py\n"
            "@@ -0,0 +1,2 @@\n+cursor = connection.cursor()\n"
            "+transaction = connection.begin()\n"
        )
        findings = runner.run_all(parser.parse_unified_diff(diff))
        by_file = {
            item["file"]: item["category"]
            for item in findings
            if item["category"] in {"security", "async_error", "database_lifecycle"}
        }
        self.assertEqual(by_file["command.js"], "security")
        self.assertEqual(by_file["jobs.py"], "async_error")
        self.assertEqual(by_file["store.py"], "database_lifecycle")

        safe = parser.parse_unified_diff(
            "--- a/command.js\n+++ b/command.js\n@@ -0,0 +1 @@\n"
            "+child_process.exec('date')\n"
        )
        self.assertNotIn(
            "security",
            {item["category"] for item in runner.run_all(safe)},
        )

    def test_formatting_only_source_change_does_not_warn_about_tests(self) -> None:
        formatting_diff = (
            "--- a/calculator.py\n+++ b/calculator.py\n@@ -1 +1 @@\n"
            "-def add(a,b):\n"
            "+def add(a, b):\n"
        )
        parsed_input = parse_diff_text(
            formatting_diff,
            kind="diff_file",
            source="formatting.diff",
            input_root=Path(self.temp_dir.name),
        )
        fake_categories = {
            item.category for item in analyze_with_fake_model(parsed_input).warnings
        }
        self.assertNotIn("test_missing", fake_categories)

        runner = self.load_skill_script("run_review_rules.py")
        skill_categories = {
            item["category"]
            for item in runner.run_all(
                _diff_parser_module().parse_unified_diff(formatting_diff)
            )
        }
        self.assertNotIn("test_missing", skill_categories)

    def test_controlled_file_reader_redacts_and_rejects_escape(self) -> None:
        module = self.load_skill_script("inspect_files.py")
        root = Path(self.temp_dir.name) / "repository"
        root.mkdir()
        (root / "settings.py").write_text(
            'API_KEY = "sk-testabcdefghijklmnop"\n'
            'JWT = "eyJheader.payload.signaturevalue"\n'
            'DATABASE_URL = "postgresql://admin:dummy-password@db.invalid/app"\n'
            'PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----ABCDEF0123456789'
            '-----END PRIVATE KEY-----"\n'
            'AWS_SECRET_ACCESS_KEY = "plain-aws-secret-material"\n'
            'CONFIG = {"apiKey": "plain-json-api-key"}\n',
            encoding="utf-8",
        )
        file_list = root / "files.txt"
        file_list.write_text("settings.py\n", encoding="utf-8")
        result = module.inspect_files(root, file_list)
        self.assertIn("REDACTED", result["files"][0]["content"])
        for secret in (
            "sk-testabcdefghijklmnop",
            "eyJheader.payload.signaturevalue",
            "dummy-password",
            "ABCDEF0123456789",
            "plain-aws-secret-material",
            "plain-json-api-key",
        ):
            self.assertNotIn(secret, result["files"][0]["content"])
        file_list.write_text("../outside.py\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            module.inspect_files(root, file_list)

        direct = module.inspect_paths(root, ["settings.py"])
        self.assertEqual(direct["files"][0]["path"], "settings.py")
        self.assertIn("REDACTED", direct["files"][0]["content"])
        with self.assertRaisesRegex(ValueError, "outside the selected Git scope"):
            module.inspect_paths(root, ["settings.py"], allowed_paths={"app.py"})
        with self.assertRaises(ValueError):
            module.inspect_paths(root, ["settings.py"] * (module.MAX_PATHS + 1))

    def test_controlled_file_reader_pages_and_rejects_symlinks(self) -> None:
        module = self.load_skill_script("inspect_files.py")
        root = Path(self.temp_dir.name) / "paged-repository"
        root.mkdir()
        paths = []
        for index in range(5):
            path = root / f"file_{index}.py"
            path.write_text(f"value = {index}\n", encoding="utf-8")
            paths.append(path.name)
        first = module.inspect_paths(root, paths)
        second = module.inspect_paths(root, paths, cursor=first["next_cursor"])
        self.assertEqual(len(first["files"]), module.MAX_PAGE_FILES)
        self.assertEqual(first["next_cursor"], module.MAX_PAGE_FILES)
        self.assertIsNone(second["next_cursor"])
        self.assertLess(
            len(__import__("json").dumps(first, ensure_ascii=False)),
            16 * 1024,
        )

        (root / ".env").write_text("UNRECOGNIZED_VALUE=dummy\n", encoding="utf-8")
        (root / "config.py").symlink_to(root / ".env")
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            module.inspect_paths(root, ["config.py"])

    def test_file_list_validator_is_bounded_and_blocks_secret_paths(self) -> None:
        module = self.load_skill_script("inspect_file_list.py")
        reader = self.load_skill_script("inspect_files.py")
        self.assertFalse(module.is_likely_secret_path("src/tokenizer.py"))
        self.assertFalse(reader._is_likely_secret_path("src/password_validator.py"))
        self.assertTrue(module.is_likely_secret_path("config/credentials.json"))
        self.assertTrue(reader._is_likely_secret_path("secrets-prod/config.json"))
        root = Path(self.temp_dir.name)
        path = root / "files.txt"
        path.write_text(
            "\n".join(f"src/file_{index}.py" for index in range(20)),
            encoding="utf-8",
        )
        files = module.parse_file_list(path)
        self.assertEqual(len(files), 20)
        path.write_text("src/app.py\n.env\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "secret"):
            module.parse_file_list(path)

    def test_host_file_list_parser_rejects_symbolic_lists(self) -> None:
        root = Path(self.temp_dir.name)
        target = root / "files.txt"
        target.write_text("app.py\n", encoding="utf-8")
        link = root / "list.txt"
        link.symlink_to(target)
        from inputs.parser import parse_file_list

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            parse_file_list(link)

    def test_bounded_runner_redacts_caps_and_marks_timeout(self) -> None:
        class Delegate:
            captured = None

            async def run_program(self, workspace, spec, context=None):
                self.captured = spec
                return WorkspaceRunResult(
                    stdout="API_KEY=sk-testabcdefghijklmnop\n" + "界" * 2000,
                    stderr="",
                    exit_code=124,
                    duration=0.1,
                )

        delegate = Delegate()
        runner = _BoundedProgramRunner(delegate, max_output_bytes=1024)
        result = asyncio.run(
            runner.run_program(
                Mock(),
                WorkspaceRunProgramSpec(
                    cmd="python3",
                    args=["-c", "print('value')"],
                    timeout=0.5,
                ),
            )
        )
        self.assertTrue(result.timed_out)
        self.assertNotIn("sk-testabcdefghijklmnop", result.stdout)
        self.assertIn("output truncated", result.stdout)
        self.assertLessEqual(len(result.stdout), 512)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 512)
        self.assertEqual(delegate.captured.timeout, 2.5)

    def test_bounded_shell_wrapper_does_not_deadlock_on_large_output(self) -> None:
        if shutil.which("bash") is None or shutil.which("timeout") is None:
            self.skipTest("bash and GNU timeout are required")
        started = time.perf_counter()
        completed = subprocess.run(
            [
                "bash",
                "-c",
                _BOUNDED_RUN_SCRIPT,
                "code-review-test",
                "512",
                "2s",
                sys.executable,
                "-c",
                "print('A' * 8192)",
            ],
            check=False,
            capture_output=True,
            timeout=5,
        )
        self.assertLess(time.perf_counter() - started, 5)
        self.assertLessEqual(len(completed.stdout), 512)

        timed_out = subprocess.run(
            [
                "bash",
                "-c",
                _BOUNDED_RUN_SCRIPT,
                "code-review-test",
                "512",
                "0.1s",
                sys.executable,
                "-c",
                "import time; time.sleep(2)",
            ],
            check=False,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(timed_out.returncode, 124)

    def test_docker_image_policy_is_bound_to_dockerfile_hash(self) -> None:
        client = object.__new__(_HardenedContainerClient)
        client.docker_path = str(EXAMPLE_ROOT / "sandbox")
        expected = __import__("hashlib").sha256(
            (EXAMPLE_ROOT / "sandbox" / "Dockerfile").read_bytes()
        ).hexdigest()
        self.assertEqual(client._expected_image_policy(), expected)

        client.image = "test-image"
        client._client = Mock()
        client._build_docker_image()
        build_args = client._client.images.build.call_args.kwargs
        self.assertEqual(
            build_args["buildargs"]["REVIEW_IMAGE_POLICY_HASH"],
            expected,
        )

    def test_governed_toolset_is_lazy_and_hides_workspace_exec(self) -> None:
        class NeverStartSandbox:
            called = False

            def create_runtime(self, repository_path, skills_path):
                self.called = True
                raise AssertionError("Docker runtime must stay lazy")

        sandbox = NeverStartSandbox()
        toolset, _repository, runtime = create_skill_tools(
            sandbox,
            EXAMPLE_ROOT,
            EXAMPLE_ROOT / "skills",
        )
        tools = asyncio.run(toolset.get_tools())
        names = {tool.name for tool in tools}
        self.assertIn("skill_run", names)
        self.assertNotIn("workspace_exec", names)
        self.assertTrue(names <= SAFE_SKILL_TOOLS)
        self.assertFalse(runtime.is_initialized)
        self.assertFalse(sandbox.called)
        skill_run = next(tool for tool in tools if tool.name == "skill_run")
        self.assertTrue(skill_run.require_skill_loaded)

    def test_governed_skill_run_blocks_before_runtime_initialization(self) -> None:
        class NeverStartSandbox:
            called = False

            def create_runtime(self, repository_path, skills_path):
                self.called = True
                raise AssertionError("blocked commands must not initialize Docker")

        sandbox = NeverStartSandbox()
        toolset, _repository, runtime = create_skill_tools(
            sandbox,
            EXAMPLE_ROOT,
            EXAMPLE_ROOT / "skills",
        )
        skill_run = next(
            tool for tool in asyncio.run(toolset.get_tools()) if tool.name == "skill_run"
        )
        session = Mock(spec=SessionABC)
        session.app_name = "test"
        session.user_id = "user"
        session.id = "session"
        session.state = {}
        agent = Mock(spec=AgentABC)
        agent.name = "review-agent"
        agent.before_tool_callback = None
        agent.after_tool_callback = None
        agent_context = new_agent_context()
        invocation = InvocationContext(
            session_service=AsyncMock(spec=SessionServiceABC),
            invocation_id="blocked-skill-run",
            agent=agent,
            agent_context=agent_context,
            session=session,
        )

        blocked_commands = (
            ("git -C /etc status", "deny"),
            ("rm -rf work", "needs_human_review"),
        )
        for command, _expected in blocked_commands:
            with self.assertRaises(PermissionError):
                asyncio.run(
                    skill_run.run_async(
                        tool_context=invocation,
                        args={"skill": "code-review", "command": command},
                    )
                )

        self.assertFalse(runtime.is_initialized)
        self.assertFalse(sandbox.called)
        decisions = agent_context.get_metadata(FILTER_DECISIONS_METADATA_KEY)
        self.assertEqual(
            [item["decision"] for item in decisions],
            [expected for _command, expected in blocked_commands],
        )

    def test_sandbox_factory_uses_environment_selection(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CODE_REVIEW_SANDBOX_BACKEND": "docker",
                "CODE_REVIEW_DOCKER_IMAGE": "review-test:local",
            },
        ):
            sandbox = create_sandbox_provider()
        self.assertIsInstance(sandbox, DockerSandbox)
        self.assertEqual(sandbox.image, "review-test:local")

        with patch.dict(
            os.environ,
            {"CODE_REVIEW_SANDBOX_BACKEND": "unsupported"},
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported sandbox backend"):
                create_sandbox_provider()

    def test_model_configuration_requires_encrypted_remote_transport(self) -> None:
        base = {
            "TRPC_AGENT_API_KEY": "dummy-key",
            "TRPC_AGENT_MODEL_NAME": "dummy-model",
        }
        with patch.dict(
            os.environ,
            {**base, "TRPC_AGENT_BASE_URL": "http://models.example.invalid/v1"},
        ):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                ModelConfig.from_env()
        with patch.dict(
            os.environ,
            {**base, "TRPC_AGENT_BASE_URL": "http://127.0.0.1:8000/v1"},
        ):
            config = ModelConfig.from_env()
        self.assertEqual(config.base_url, "http://127.0.0.1:8000/v1")
        with patch.dict(
            os.environ,
            {
                **base,
                "TRPC_AGENT_BASE_URL": "https://models.example.invalid/v1",
                "TRPC_AGENT_ALLOWED_MODEL_HOSTS": "trusted.example.invalid",
            },
        ):
            with self.assertRaisesRegex(ValueError, "ALLOWED_MODEL_HOSTS"):
                ModelConfig.from_env()

    def test_storage_factory_supports_schema_path_configuration(self) -> None:
        root = Path(self.temp_dir.name)
        schema = EXAMPLE_ROOT / "storage" / "schema.sql"
        with patch.dict(
            os.environ,
            {
                "CODE_REVIEW_STORAGE_BACKEND": "sqlite",
                "CODE_REVIEW_SQLITE_PATH": str(root / "configured.sqlite3"),
                "CODE_REVIEW_SQLITE_SCHEMA_PATH": str(schema),
            },
        ):
            store = create_review_store()
        self.assertIsInstance(store, SQLiteReviewStore)
        self.assertEqual(store.schema_path, schema)
        store.initialize()
        self.assertTrue(store.database_path.is_file())

    def test_storage_factory_selects_postgresql_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CODE_REVIEW_STORAGE_BACKEND": "postgresql",
                "CODE_REVIEW_POSTGRES_DSN": (
                    "postgresql://reviewer:local-test@127.0.0.1/reviews"
                ),
                "CODE_REVIEW_POSTGRES_CONNECT_TIMEOUT_SECONDS": "7",
                "CODE_REVIEW_POSTGRES_STATEMENT_TIMEOUT_SECONDS": "20",
            },
        ):
            store = create_review_store()
        self.assertIsInstance(store, PostgreSQLReviewStore)
        self.assertEqual(store.connect_timeout_seconds, 7)
        self.assertEqual(store.statement_timeout_seconds, 20)

        with patch.dict(
            os.environ,
            {
                "CODE_REVIEW_STORAGE_BACKEND": "postgres",
                "CODE_REVIEW_POSTGRES_DSN": (
                    "postgresql://reviewer:local-test@localhost/reviews"
                ),
            },
        ):
            with self.assertRaisesRegex(ValueError, "SQLite"):
                create_review_store(Path("override.sqlite3"))

    def test_postgresql_configuration_enforces_safe_dsn_and_timeouts(self) -> None:
        with self.assertRaisesRegex(ValueError, "required"):
            validate_postgres_dsn("")
        with self.assertRaisesRegex(ValueError, "postgresql://"):
            validate_postgres_dsn("host=localhost dbname=reviews")
        with self.assertRaisesRegex(ValueError, "sslmode"):
            validate_postgres_dsn(
                "postgresql://reviewer:example@db.example.invalid/reviews"
            )
        secure = (
            "postgresql://reviewer:example@db.example.invalid/reviews"
            "?sslmode=verify-full"
        )
        self.assertEqual(validate_postgres_dsn(secure), secure)
        with patch.dict(
            os.environ,
            {
                "CODE_REVIEW_STORAGE_BACKEND": "postgresql",
                "CODE_REVIEW_POSTGRES_DSN": (
                    "postgresql://reviewer:local-test@localhost/reviews"
                ),
                "CODE_REVIEW_POSTGRES_CONNECT_TIMEOUT_SECONDS": "31",
            },
        ):
            with self.assertRaisesRegex(ValueError, "between 1 and 30"):
                create_review_store()

    def test_postgresql_schema_is_confined_and_statement_allowlisted(self) -> None:
        root = Path(self.temp_dir.name)
        external = root / "postgres.sql"
        external.write_text("DROP TABLE public.review_tasks;", encoding="utf-8")
        store = PostgreSQLReviewStore(
            "postgresql://reviewer:local-test@localhost/reviews",
            schema_path=external,
        )
        with self.assertRaisesRegex(ValueError, "storage directory"):
            store._schema_statements()

        with patch(
            "storage.postgresql.read_trusted_schema",
            return_value="DROP TABLE public.review_tasks;",
        ):
            store = PostgreSQLReviewStore(
                "postgresql://reviewer:local-test@localhost/reviews",
            )
            with self.assertRaisesRegex(ValueError, "disallowed"):
                store._schema_statements()

    def test_postgresql_connection_errors_redact_dsn_credentials(self) -> None:
        marker = "sk-postgres-connection-fake-secret-1234567890"
        store = PostgreSQLReviewStore(
            f"postgresql://reviewer:{marker}@localhost/reviews"
        )

        class FailingDriver:
            @staticmethod
            def connect(dsn, **kwargs):
                del kwargs
                raise RuntimeError(f"failed to connect with {dsn}")

        with patch.object(
            store,
            "_load_driver",
            return_value=(FailingDriver, None),
        ):
            with self.assertRaises(RuntimeError) as context:
                store._connect()
        self.assertNotIn(marker, str(context.exception))
        self.assertIn("[REDACTED]", str(context.exception))

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, exception_type, exception, traceback):
                del exception_type, exception, traceback
                return False

        with patch.object(store, "_connect", return_value=FakeConnection()):
            with self.assertRaises(RuntimeError) as operation_context:
                with store._operation("test write"):
                    raise ValueError(f"password={marker}")
        self.assertNotIn(marker, str(operation_context.exception))
        self.assertIn("[REDACTED]", str(operation_context.exception))

    def test_sqlite_enables_wal_and_digest_profile_index(self) -> None:
        self.store.initialize()
        with self.store._connect() as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list(review_inputs)")
            }
            connection.execute(
                "INSERT INTO review_tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("mode-test", "now", "now", "running", "repo", "changed", ""),
            )
            connection.commit()
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{self.store.database_path}{suffix}")
                self.assertTrue(sidecar.is_file())
                self.assertEqual(sidecar.stat().st_mode & 0o077, 0)
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertIn("idx_review_inputs_digest_profile", indexes)

    def test_sqlite_rejects_symbolic_database_paths(self) -> None:
        root = Path(self.temp_dir.name)
        target = root / "target.sqlite3"
        target.write_bytes(b"")
        link = root / "link.sqlite3"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular file"):
            SQLiteReviewStore(link).initialize()

    def test_sqlite_rejects_non_database_files_and_external_schema(self) -> None:
        root = Path(self.temp_dir.name)
        existing = root / "not-a-database.sqlite3"
        existing.write_text("do not overwrite", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "non-SQLite"):
            SQLiteReviewStore(existing).initialize()

        schema = root / "external-schema.sql"
        schema.write_text("CREATE TABLE example(value TEXT);", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "storage directory"):
            SQLiteReviewStore(root / "new.sqlite3", schema_path=schema).initialize()

    def test_report_writer_rejects_symbolic_output_directory(self) -> None:
        root = Path(self.temp_dir.name)
        target = root / "target"
        target.mkdir()
        output_link = root / "reports-link"
        output_link.symlink_to(target, target_is_directory=True)
        report = ReviewReport.model_validate_json(
            (EXAMPLE_ROOT / "examples" / "review_report.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaisesRegex(ValueError, "not a link"):
            ReportWriter(output_link).write(report)

    def test_input_parse_failure_is_audited(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid fixture"):
            asyncio.run(
                self.workflow.run(
                    ReviewRequest(fixture="../invalid", fake_model=True)
                )
            )
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT status, conclusion FROM review_tasks"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "failed")
        self.assertIn("Invalid fixture", rows[0][1])

    def test_sample_report_matches_schema(self) -> None:
        sample = EXAMPLE_ROOT / "examples" / "review_report.json"
        report = ReviewReport.model_validate_json(sample.read_text(encoding="utf-8"))
        self.assertEqual(report.task_id, "sample-task")

    def test_agent_output_schema_builds_sdk_response_tool(self) -> None:
        declaration = SetModelResponseTool(ReviewAnalysis)._get_declaration()
        self.assertIn("findings", declaration.parameters.properties)

    def test_markdown_contains_required_audit_sections(self) -> None:
        result = self.run_fixture("security")
        markdown = result.artifacts.markdown_path.read_text(encoding="utf-8")
        for heading in (
            "## Findings",
            "## Warnings",
            "## Needs Human Review",
            "## Filter Decisions",
            "## Sandbox Runs",
            "## Monitoring",
            "## Conclusion",
        ):
            self.assertIn(heading, markdown)

    def test_markdown_escapes_model_controlled_structure(self) -> None:
        result = self.run_fixture("security")
        hostile = result.report.model_copy(
            update={
                "analysis": result.report.analysis.model_copy(
                    update={"summary": "# forged heading\n<script>alert(1)</script>"}
                ),
                "conclusion": "[forged](https://invalid.example)",
            }
        )
        writer = ReportWriter(Path(self.temp_dir.name) / "hostile-reports")
        markdown = writer.write(hostile).markdown_path.read_text(encoding="utf-8")
        self.assertNotIn("\n# forged heading", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("[forged](https://invalid.example)", markdown)

    def test_rule_runner_pages_large_output_as_valid_json(self) -> None:
        runner = self.load_skill_script("run_review_rules.py")
        parser = _diff_parser_module()
        lines = "".join(f"+value_{index} = {index}\n" for index in range(800))
        parsed = parser.parse_unified_diff(
            "--- /dev/null\n+++ b/large.py\n@@ -0,0 +1,800 @@\n" + lines
        )
        page = runner.build_page(parsed)
        encoded = __import__("json").dumps(page, ensure_ascii=False)
        self.assertLess(len(encoded), 16 * 1024)
        self.assertIsNotNone(page["next_cursor"])
        last = runner.build_page(
            parsed,
            cursor=page["total_records"] - 1,
        )
        self.assertIsNone(last["next_cursor"])

    def test_report_write_failure_marks_started_task_failed(self) -> None:
        class FailingWriter:
            def write(self, report):
                raise OSError("simulated report failure")

        workflow = CodeReviewWorkflow(
            model_config=None,
            sandbox=None,
            store=self.store,
            report_writer=FailingWriter(),
            skills_path=EXAMPLE_ROOT / "skills",
        )
        with self.assertRaisesRegex(OSError, "simulated report failure"):
            asyncio.run(
                workflow.run(ReviewRequest(fixture="clean", fake_model=True))
            )
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT status, conclusion FROM review_tasks "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], "failed")
        self.assertIn("simulated report failure", row[1])

    def test_fake_workflow_finishes_under_two_minutes(self) -> None:
        started = time.perf_counter()
        self.run_fixture("clean")
        self.assertLess(time.perf_counter() - started, 120)

    def test_dry_run_executes_complete_non_network_chain(self) -> None:
        result = asyncio.run(
            self.workflow.run(ReviewRequest(fixture="security", dry_run=True))
        )
        self.assertEqual(result.report.sandbox_runs[0].status, "simulated")
        self.assertTrue(result.report.filter_decisions)
        self.assertTrue(result.report.analysis.findings)
        self.assertIsNotNone(self.store.get_task_details(result.report.task_id))

    def test_no_model_environment_is_required(self) -> None:
        saved = {key: os.environ.pop(key, None) for key in (
            "TRPC_AGENT_API_KEY",
            "TRPC_AGENT_BASE_URL",
            "TRPC_AGENT_MODEL_NAME",
        )}
        try:
            result = self.run_fixture("clean")
            self.assertEqual(result.report.status, "completed")
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_local_env_loader_preserves_process_precedence(self) -> None:
        path = Path(self.temp_dir.name) / ".env"
        path.write_text(
            "# local configuration\n"
            "CODE_REVIEW_TEST_NEW='loaded=value'\n"
            "export CODE_REVIEW_TEST_EXISTING=from-file\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        with patch.dict(
            os.environ,
            {"CODE_REVIEW_TEST_EXISTING": "from-process"},
        ):
            load_env_file(path)
            self.assertEqual(os.environ["CODE_REVIEW_TEST_NEW"], "loaded=value")
            self.assertEqual(
                os.environ["CODE_REVIEW_TEST_EXISTING"],
                "from-process",
            )

    def test_local_env_loader_rejects_malformed_entries(self) -> None:
        path = Path(self.temp_dir.name) / ".env"
        path.write_text("INVALID ENTRY\n", encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "Invalid .env entry"):
            load_env_file(path)

    def test_local_env_loader_rejects_unsafe_files_and_keys(self) -> None:
        path = Path(self.temp_dir.name) / ".env"
        path.write_text("CODE_REVIEW_TEST=value\n", encoding="utf-8")
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "permissions"):
            load_env_file(path)

        path.chmod(0o600)
        path.write_text("PYTHONPATH=/tmp/untrusted\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported .env key"):
            load_env_file(path)

        link = Path(self.temp_dir.name) / ".env-link"
        link.symlink_to(path)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            load_env_file(link)

    def test_default_repository_is_nearest_worktree(self) -> None:
        root = Path(self.temp_dir.name) / "repository"
        nested = root / "src" / "package"
        (root / ".git").mkdir(parents=True)
        nested.mkdir(parents=True)
        self.assertEqual(find_git_worktree(nested), root)

    def test_sandbox_module_import_has_no_order_dependency(self) -> None:
        repository_root = EXAMPLE_ROOT.parents[1]
        python_path = os.pathsep.join(
            item
            for item in (
                str(repository_root),
                os.environ.get("PYTHONPATH", ""),
            )
            if item
        )
        result = subprocess.run(
            [sys.executable, "-c", "from sandbox.docker import DockerSandbox"],
            cwd=EXAMPLE_ROOT,
            env={**os.environ, "PYTHONPATH": python_path},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
