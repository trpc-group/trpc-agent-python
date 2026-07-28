"""Backend-neutral sandbox task and result models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ...agent.models import FilterDecision


class ResourcePolicy(BaseModel):
    timeout_seconds: float = 30.0
    cpu_percent: int = 100
    memory_mb: int = 512
    max_pids: int = 64
    max_output_bytes: int = 64_000


class SandboxTask(BaseModel):
    id: str
    task_type: str
    command: list[str]
    cwd: str
    input_paths: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    network_targets: list[str] = Field(default_factory=list)
    resources: ResourcePolicy = Field(default_factory=ResourcePolicy)


class SandboxExecutionResult(BaseModel):
    task_id: str
    status: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    error_type: str | None = None
    decision: FilterDecision
