# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Dry-run review pipeline for the code review example."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .diff_parser import parse_unified_diff
from .fake_reviewer import review_with_fake_model
from .filters import apply_post_filters
from .governance import build_default_sandbox_requests
from .governance import evaluate_sandbox_requests
from .inputs import build_review_input
from .report import build_report
from .rules import review_with_rules
from .sandbox import create_sandbox_runner
from .schemas import AuditEvent
from .schemas import ParsedDiff
from .schemas import ReviewInput
from .schemas import ReviewReport
from .schemas import ReviewTaskStatus
from .schemas import SandboxPolicy
from .storage import ReviewStorage


@dataclass(frozen=True)
class ReviewRunConfig:
    """Configuration for a full deterministic review run."""

    fake_model: bool = True
    sandbox_policy: SandboxPolicy | None = None
    db_path: Path | None = None
    task_id: str | None = None
    include_missing_tests: bool = True
    container_image: str = "python:3-slim"


def run_dry_review(diff_text: str, *, fake_model: bool = True) -> ReviewReport:
    """Run the deterministic dry-run review pipeline."""
    if not fake_model:
        raise NotImplementedError("Only --fake-model dry-run mode is implemented in this MVP.")

    parsed_diff = parse_unified_diff(diff_text)
    raw_findings = review_with_fake_model(parsed_diff)
    post_result = apply_post_filters(raw_findings, parsed_diff)
    return build_report(
        parsed_diff,
        post_result.findings,
        post_result.warnings,
        post_result.decisions,
        redaction_count=post_result.redaction_count,
    )


def run_review(
    diff_text: str,
    *,
    parsed_diff: ParsedDiff | None = None,
    review_input: ReviewInput | None = None,
    config: ReviewRunConfig | None = None,
) -> ReviewReport:
    """Run the full deterministic review pipeline with governance, sandbox, and optional storage."""
    config = config or ReviewRunConfig()
    if not config.fake_model:
        raise NotImplementedError("Only fake-model dry-run mode is implemented in this example.")

    started = time.monotonic()
    task_id = config.task_id or f"review-{uuid.uuid4().hex[:12]}"
    parsed = parsed_diff or parse_unified_diff(diff_text)
    review_input = review_input or build_review_input(
        parsed,
        diff_text=diff_text,
        input_type="diff_text",
    )
    policy = config.sandbox_policy or SandboxPolicy()
    storage = ReviewStorage(config.db_path) if config.db_path else None
    audit_events: list[AuditEvent] = []

    try:
        if storage:
            storage.create_task(task_id=task_id, status=ReviewTaskStatus.RUNNING, mode="dry_run", review_input=review_input)

        requests = build_default_sandbox_requests(review_input.changed_files)
        allowed_requests, pre_decisions = evaluate_sandbox_requests(requests, policy)
        sandbox_runner = create_sandbox_runner(policy, container_image=config.container_image)
        sandbox_runs = sandbox_runner.run_requests(allowed_requests, parsed, diff_text)
        for run in sandbox_runs:
            if run.error_type:
                audit_events.append(
                    AuditEvent(
                        event_type="sandbox_run_failed",
                        severity="warning",
                        message=f"Sandbox script {run.script_name} finished with {run.error_type}.",
                        details={"script_name": run.script_name, "exit_code": run.exit_code},
                    )
                )

        raw_findings = review_with_rules(parsed, include_missing_tests=config.include_missing_tests)
        post_result = apply_post_filters(raw_findings, parsed)
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        report = build_report(
            parsed,
            post_result.findings,
            post_result.warnings,
            [*pre_decisions, *post_result.decisions],
            task_id=task_id,
            review_input=review_input,
            sandbox_runs=sandbox_runs,
            audit_events=audit_events,
            duration_ms=duration_ms,
            redaction_count=post_result.redaction_count,
        )

        if storage:
            storage.record_sandbox_runs(task_id, sandbox_runs)
            storage.record_filter_decisions(task_id, report.filter_decisions)
            storage.record_findings(task_id, report.findings, is_warning=False)
            storage.record_findings(task_id, report.warnings, is_warning=True)
            storage.record_audit_events(task_id, audit_events)
            storage.record_report(task_id, report)
            storage.update_task_status(task_id, report.status, duration_ms=duration_ms)
        return report
    except Exception as exc:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        if storage:
            storage.update_task_status(
                task_id,
                ReviewTaskStatus.FAILED,
                duration_ms=duration_ms,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        raise
    finally:
        if storage:
            storage.close()
