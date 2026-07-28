"""Executor backed by the SDK's common Workspace Runtime interface."""

from __future__ import annotations

import time
from typing import Any

from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec

from ..models import SandboxExecutionResult, SandboxTask
from ..policy import to_workspace_limits
from .base import SandboxExecutor


class WorkspaceExecutor(SandboxExecutor):
    def __init__(self, runtime: Any) -> None:
        self._runner = runtime.runner()

    async def execute(self, workspace: Any, task: SandboxTask, decision: Any) -> SandboxExecutionResult:
        started = time.monotonic()
        try:
            result = await self._runner.run_program(
                workspace,
                WorkspaceRunProgramSpec(
                    cmd=task.command[0],
                    args=task.command[1:],
                    cwd=task.cwd,
                    env=task.env,
                    timeout=task.resources.timeout_seconds,
                    limits=to_workspace_limits(task.resources),
                ),
            )
            duration_ms = round((time.monotonic() - started) * 1000)
            stdout = result.stdout[: task.resources.max_output_bytes]
            stderr = result.stderr[: task.resources.max_output_bytes]
            truncated = len(result.stdout) > len(stdout) or len(result.stderr) > len(stderr)
            status = "timed_out" if result.timed_out else ("completed" if result.exit_code == 0 else "failed")
            return SandboxExecutionResult(
                task_id=task.id,
                status=status,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                duration_ms=duration_ms,
                stdout=stdout,
                stderr=stderr,
                output_truncated=truncated,
                error_type="execution_timeout" if result.timed_out else None,
                decision=decision,
            )
        except Exception as exc:
            return SandboxExecutionResult(
                task_id=task.id,
                status="failed",
                duration_ms=round((time.monotonic() - started) * 1000),
                stderr=str(exc),
                error_type=type(exc).__name__,
                decision=decision,
            )
