#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""End-to-end deterministic code review workflow."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator
from trpc_agent_sdk.telemetry import tracer

from .core import InputResolver
from .core import MonitoringSummary
from .core import ResolvedInput
from .core import ReviewReport
from .core import normalize_findings
from .core import severity_distribution
from .reporting import sanitize_report
from .reporting import write_reports
from .sandbox import SandboxExecutor
from .storage import SqlReviewStore

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewConfig(BaseModel):
    """Configuration with production-safe defaults."""

    runtime: str = "container"
    db_url: str = f"sqlite:///{EXAMPLE_ROOT / 'data' / 'reviews.db'}"
    output_dir: Path = EXAMPLE_ROOT / "output"
    skill_root: Path = EXAMPLE_ROOT / "skills"
    work_root: Path = EXAMPLE_ROOT / "data" / "workspaces"
    checker_script: str = "scripts/review_diff.py"
    allowed_scripts: set[str] = Field(
        default_factory=lambda: {
            "scripts/review_diff.py",
            "scripts/timeout_probe.py",
        })
    env_allowlist: set[str] = Field(default_factory=lambda: {"PYTHONUNBUFFERED"})
    network_allowlist: set[str] = Field(default_factory=set)
    sandbox_environment: dict[str, str] = Field(default_factory=lambda: {"PYTHONUNBUFFERED": "1"})
    network_hosts: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, ge=1)
    max_timeout_seconds: int = Field(default=120, ge=1)
    max_output_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    max_policy_output_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    dry_run: bool = False
    fake_model: bool = False

    @model_validator(mode="after")
    def validate_policy(self) -> "ReviewConfig":
        if self.runtime not in {"container", "local"}:
            raise ValueError("runtime must be container or local")
        return self


class CodeReviewAgent:
    """Orchestrate input parsing, Filter, Skill sandbox, storage, and reports."""

    def __init__(self, config: ReviewConfig) -> None:
        self.config = config
        self.input_resolver = InputResolver()

    async def review(
        self,
        *,
        diff_file: Optional[Path] = None,
        repo_path: Optional[Path] = None,
        files: Optional[list[Path]] = None,
        fixture: Optional[Path] = None,
    ) -> ReviewReport:
        """Run one complete review without requiring a model API key."""

        resolved = self._resolve_input(
            diff_file=diff_file,
            repo_path=repo_path,
            files=files,
            fixture=fixture,
        )
        task_id = f"cr_{uuid.uuid4().hex}"
        created_at = _utc_now()
        started = time.monotonic()
        model_mode = "fake" if self.config.fake_model or self.config.dry_run else "deterministic"
        store = SqlReviewStore(self.config.db_url)
        staged_diff = self._stage_input(task_id, resolved)

        with tracer.start_as_current_span("code_review.review") as span:
            span.set_attribute("code_review.task_id", task_id)
            span.set_attribute("code_review.runtime", self.config.runtime)
            span.set_attribute("code_review.input_sha256", resolved.summary.sha256)
            try:
                await store.initialize()
                await store.create_task(task_id, resolved.summary, model_mode, created_at)

                executor = SandboxExecutor(
                    runtime=self.config.runtime,
                    skill_root=self.config.skill_root,
                    work_root=self.config.work_root,
                    allowed_scripts=self.config.allowed_scripts,
                    env_allowlist=self.config.env_allowlist,
                    network_allowlist=self.config.network_allowlist,
                    max_timeout_seconds=self.config.max_timeout_seconds,
                    max_output_bytes=self.config.max_output_bytes,
                    max_policy_output_bytes=self.config.max_policy_output_bytes,
                )
                execution = await executor.execute(
                    diff_path=staged_diff,
                    task_id=task_id,
                    checker_script=self.config.checker_script,
                    timeout_seconds=self.config.timeout_seconds,
                    environment=self.config.sandbox_environment,
                    network_hosts=self.config.network_hosts,
                )
                await store.add_filter_decision(task_id, execution.decision)

                filter_decisions = [execution.decision]
                sandbox_runs = []
                findings = []
                warnings = []
                operational_warnings = []
                human_review = []
                status = "completed"
                exception_distribution: dict[str, int] = {}
                normalize_redactions = 0

                if execution.decision.decision != "allow":
                    status = "blocked"
                    human_review.append(
                        f"Filter {execution.decision.rule_id}: {execution.decision.reason}"
                    )
                elif execution.run is not None:
                    sandbox_runs.append(execution.run)
                    await store.add_sandbox_run(task_id, execution.run)
                    findings, warnings, normalize_redactions = normalize_findings(execution.raw_findings)
                    if execution.run.status != "completed":
                        status = "completed_with_warnings"
                        warning = (
                            f"Sandbox {execution.run.status}; findings may be incomplete. "
                            f"Error type: {execution.run.error_type or 'unknown'}."
                        )
                        operational_warnings.append(warning)
                        human_review.append(warning)
                    if execution.run.output_truncated:
                        status = "completed_with_warnings"
                        warning = "Sandbox output reached the configured size limit; findings may be incomplete."
                        operational_warnings.append(warning)
                        human_review.append(warning)
                    if execution.run.error_type:
                        exception_distribution[execution.run.error_type] = 1
                else:
                    status = "completed_with_warnings"
                    warning = "Filter allowed execution but no sandbox result was produced."
                    operational_warnings.append(warning)
                    human_review.append(warning)

                if warnings:
                    human_review.extend(
                        f"{item.file}:{item.line} {item.title} ({item.confidence:.2f})"
                        for item in warnings)
                if self.config.runtime == "local":
                    operational_warnings.append(
                        "Local runtime is an explicit development fallback; use container in production."
                    )

                elapsed_ms = int((time.monotonic() - started) * 1000)
                metrics = MonitoringSummary(
                    total_duration_ms=elapsed_ms,
                    sandbox_duration_ms=sum(item.duration_ms for item in sandbox_runs),
                    tool_calls=execution.tool_calls,
                    interception_count=int(execution.decision.decision != "allow"),
                    finding_count=len(findings),
                    warning_count=len(warnings),
                    severity_distribution=severity_distribution(findings + warnings),
                    exception_distribution=exception_distribution,
                    redaction_count=execution.redaction_count + normalize_redactions,
                )
                conclusion = self._conclusion(status, findings, warnings)
                report = ReviewReport(
                    task_id=task_id,
                    status=status,
                    model_mode=model_mode,
                    input_summary=resolved.summary,
                    findings=findings,
                    warnings=warnings,
                    needs_human_review=human_review,
                    filter_decisions=filter_decisions,
                    sandbox_runs=sandbox_runs,
                    monitoring=metrics,
                    operational_warnings=operational_warnings,
                    conclusion=conclusion,
                    generated_at=_utc_now(),
                )
                report = sanitize_report(report)
                write_reports(report, self.config.output_dir)

                await store.add_findings(task_id, report.findings, report.warnings)
                await store.save_metrics(task_id, report.monitoring)
                await store.save_report(report)
                await store.complete_task(
                    task_id,
                    report.status,
                    report.monitoring.total_duration_ms,
                    report.generated_at,
                )
                span.set_attribute("code_review.status", report.status)
                span.set_attribute("code_review.finding_count", len(report.findings))
                span.set_attribute("code_review.interception_count", metrics.interception_count)
                return report
            finally:
                await store.close()
                staged_diff.unlink(missing_ok=True)
                staged_diff.parent.rmdir()

    def _resolve_input(
        self,
        *,
        diff_file: Optional[Path],
        repo_path: Optional[Path],
        files: Optional[list[Path]],
        fixture: Optional[Path],
    ) -> ResolvedInput:
        provided = sum(value is not None and value != [] for value in (diff_file, repo_path, files, fixture))
        if provided != 1:
            raise ValueError("provide exactly one of diff_file, repo_path, files, or fixture")
        if diff_file is not None:
            return self.input_resolver.resolve_diff_file(diff_file)
        if fixture is not None:
            return self.input_resolver.resolve_diff_file(fixture, input_type="fixture")
        if repo_path is not None:
            return self.input_resolver.resolve_repo(repo_path)
        return self.input_resolver.resolve_files(files or [])

    def _stage_input(self, task_id: str, resolved: ResolvedInput) -> Path:
        input_dir = self.config.work_root.expanduser().resolve() / "inputs" / task_id
        input_dir.mkdir(parents=True, exist_ok=True)
        path = input_dir / "review.diff"
        path.write_text(resolved.diff_text, encoding="utf-8")
        path.chmod(0o600)
        return path

    @staticmethod
    def _conclusion(status: str, findings, warnings) -> str:
        if status == "blocked":
            return "blocked_by_filter"
        if any(item.severity in {"critical", "high"} for item in findings):
            return "changes_requested"
        if findings or warnings or status == "completed_with_warnings":
            return "needs_human_review"
        return "approved"
