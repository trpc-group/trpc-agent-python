"""Policy → executor orchestration for every sandbox task type."""

from __future__ import annotations

from typing import Any

from ..agent.models import Decision, ExecutionBudget, ExecutionRequest
from ..agent.policy import ReviewExecutionFilter
from .executor import WorkspaceExecutor
from .models import SandboxExecutionResult, SandboxTask


class SandboxRunner:
    def __init__(self, runtime: Any, *, budget: ExecutionBudget | None = None) -> None:
        self.executor = WorkspaceExecutor(runtime)
        self.budget = budget or ExecutionBudget()

    async def run(self, workspace: Any, task: SandboxTask, *, dry_run: bool = False) -> SandboxExecutionResult:
        request = ExecutionRequest(
            task_id=task.id,
            command=task.command,
            cwd=task.cwd,
            input_paths=task.input_paths,
            network_targets=task.network_targets,
            env=task.env,
            timeout=task.resources.timeout_seconds,
            memory_limit_mb=task.resources.memory_mb,
        )
        decision = ReviewExecutionFilter(task.cwd, budget=self.budget).run(request)
        if decision.decision != Decision.ALLOW or dry_run:
            return SandboxExecutionResult(
                task_id=task.id,
                status="dry_run" if dry_run else "blocked",
                decision=decision,
            )
        result = await self.executor.execute(workspace, task, decision)
        self.budget.seconds_used += result.duration_ms / 1000
        return result
