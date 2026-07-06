"""Minimal dry-run sandbox runner abstraction.

This module intentionally does not execute untrusted code. It only records a
structured dry-run result so later phases can replace the runner with a real
container or Cube workspace runtime.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .filtering import FilterDecision
from .findings import Finding


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass(frozen=True)
class SandboxRun:
    """Structured summary of a sandbox runner invocation."""

    runner_name: str
    timeout_seconds: int
    status: str
    started_at: str
    finished_at: str
    stdout_summary: str
    stderr_summary: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DryRunSandboxRunner:
    """A fake sandbox runner that records a completed static-check simulation."""

    def __init__(self, timeout_seconds: int = 30) -> None:
        self.runner_name = "dry-run"
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        *,
        files: Sequence[str],
        findings: Sequence[Finding],
        filter_decision: FilterDecision,
    ) -> SandboxRun:
        started_at = _utc_now()
        finished_at = _utc_now()
        if filter_decision.decision == "deny":
            return SandboxRun(
                runner_name=self.runner_name,
                timeout_seconds=self.timeout_seconds,
                status="skipped",
                started_at=started_at,
                finished_at=finished_at,
                stdout_summary="dry-run sandbox skipped because filter denied the review",
                stderr_summary=filter_decision.reason,
            )
        return SandboxRun(
            runner_name=self.runner_name,
            timeout_seconds=self.timeout_seconds,
            status="completed",
            started_at=started_at,
            finished_at=finished_at,
            stdout_summary=f"simulated static checks for {len(files)} files and {len(findings)} findings",
            stderr_summary="",
        )
