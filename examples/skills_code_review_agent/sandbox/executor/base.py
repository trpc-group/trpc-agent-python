"""Executor contract independent of Container, Cube, and local backends."""

from abc import ABC, abstractmethod
from typing import Any

from ..models import SandboxExecutionResult, SandboxTask


class SandboxExecutor(ABC):
    @abstractmethod
    async def execute(
        self,
        workspace: Any,
        task: SandboxTask,
        decision: Any,
    ) -> SandboxExecutionResult:
        """Execute one already-authorized task."""
