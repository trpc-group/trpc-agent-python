"""Application adapter for the layered sandbox package."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import create_local_workspace_runtime

from ..sandbox import SandboxRunner
from ..sandbox.runtime import WorkspaceManager
from .task_planner import ReviewTaskPlanner
from .metrics import MetricsCollector
from .models import Decision, ExecutionBudget, Finding, ReviewInput, SandboxRunResult
from .sanitizer import redact_sensitive_text


async def run_sandbox_checks(
    runtime: Any,
    review_input: ReviewInput,
    *,
    runtime_name: str = "local",
    dry_run: bool = False,
    metrics: MetricsCollector | None = None,
    budget: ExecutionBudget | None = None,
) -> tuple[list[SandboxRunResult], list[Finding], list[str]]:
    """Run the code-review custom-rule task and map it to application models."""
    metrics = metrics or MetricsCollector()
    warnings: list[str] = []
    if runtime is None:
        if runtime_name != "local":
            return [], [], [f"{runtime_name} runtime is required; rule check was skipped"]
        runtime = create_local_workspace_runtime()

    workspaces = WorkspaceManager(runtime)
    skill_root = Path(__file__).parents[1] / "skills" / "code-review"
    project_source = (
        Path(review_input.source_path)
        if review_input.source_type in {"repo", "file_list"} and review_input.source_path
        else None
    )
    directories = {"code-review": skill_root}
    if project_source and project_source.is_dir():
        directories["project"] = project_source
    prepared = await workspaces.prepare(
        review_input.digest[:32],
        directories=directories,
        files={"review.json": review_input.model_dump_json().encode()},
    )
    try:
        plan = ReviewTaskPlanner().build_plan(
            review_input,
            prepared.info.path,
            runtime_name,
            project_staged="project" in directories,
        )
        findings: list[Finding] = []
        runs: list[SandboxRunResult] = []
        runner = SandboxRunner(runtime, budget=budget)
        for task in plan.tasks:
            result = await runner.run(prepared.info, task, dry_run=dry_run)
            metrics.record_tool(blocked=result.decision.decision != Decision.ALLOW)
            metrics.record_stage(task.task_type, result.duration_ms / 1000)
            if result.error_type:
                metrics.record_error(result.error_type)
            if result.status in {"failed", "timed_out", "blocked"}:
                detail = result.error_type or (
                    f"exit code {result.exit_code}" if result.exit_code is not None else result.status
                )
                warnings.append(f"{task.task_type} check {result.status}: {detail}")
            if task.task_type == "custom_rule" and result.status == "completed":
                try:
                    findings = [Finding.model_validate(item) for item in json.loads(result.stdout)]
                except Exception as exc:
                    metrics.record_error(exc)
                    warnings.append(f"rule check output is invalid: {type(exc).__name__}")
            runs.append(
                SandboxRunResult(
                    id=result.task_id,
                    runtime=runtime_name,
                    task_type=task.task_type,
                    command=task.command,
                    status=result.status,
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                    duration_ms=result.duration_ms,
                    stdout_summary=redact_sensitive_text(result.stdout),
                    stderr_summary=redact_sensitive_text(result.stderr),
                    decision=result.decision,
                )
            )
            if result.output_truncated:
                warnings.append(f"{task.task_type} output was truncated")
        return runs, findings, warnings
    finally:
        try:
            await workspaces.cleanup(prepared)
        except Exception as exc:
            metrics.record_error(exc)
            warnings.append(f"workspace cleanup failed: {type(exc).__name__}")
