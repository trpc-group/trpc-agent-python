"""Sandbox extension point."""

from pathlib import Path
from typing import Protocol

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime


class SandboxProvider(Protocol):
    """Create an isolated runtime for one review target."""

    def create_runtime(
        self,
        repository_path: Path,
        skills_path: Path,
    ) -> BaseWorkspaceRuntime:
        """Create a runtime with read-only repository and Skill mounts."""
        ...
