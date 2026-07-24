"""Workspace-style sandbox execution backends for the code review example.

The container backend runs the same ``skills/``, ``work/`` and ``out/`` layout
inside Docker with network disabled. Dry-run mode uses the same layout in a
temporary local workspace as a development fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .filtering import ReviewExecutionFilter
from .models import FilterDecision
from .models import SandboxRequest
from .models import SandboxRun
from .redaction import redact_text


SAFE_ENV_KEYS = {
    "PATH",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
}


class SandboxRunner:
    """Run code-review skill scripts with filter, timeout and output limits."""

    def __init__(
        self,
        *,
        runtime: str,
        skill_dir: Path,
        execution_filter: ReviewExecutionFilter,
        allow_local_fallback: bool = False,
    ) -> None:
        self.runtime = runtime
        self.skill_dir = skill_dir
        self.execution_filter = execution_filter
        self.allow_local_fallback = allow_local_fallback

    def run(self, request: SandboxRequest) -> SandboxRun:
        """Filter and execute one sandbox request."""
        decision = self.execution_filter.evaluate_request(request)
        if not decision.allowed:
            return SandboxRun(
                name=request.name,
                runtime=self.runtime,
                command=request.display_command,
                status="filtered",
                filter_decision=decision,
                error_type="FilterIntercept",
            )

        if self.runtime == "container":
            return self._run_container(request, decision)
        if self.runtime in {"local", "dry-run-local", "auto"}:
            return self._run_local(request, decision, runtime_name="dry-run-local" if self.runtime == "auto" else self.runtime)
        return SandboxRun(
            name=request.name,
            runtime=self.runtime,
            command=request.display_command,
            status="failed",
            exit_code=None,
            stderr=f"unsupported sandbox runtime: {self.runtime}",
            error_type="UnsupportedRuntime",
            filter_decision=decision,
        )

    def _run_local(self, request: SandboxRequest, decision: FilterDecision, *, runtime_name: str) -> SandboxRun:
        started = time.monotonic()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with tempfile.TemporaryDirectory(prefix="code_review_sandbox_") as tmp:
            workspace = Path(tmp)
            self._prepare_workspace(workspace, request)
            command = self._resolve_command(request.command)
            cwd = workspace / request.cwd
            env = self._safe_env(request.env)
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(cwd),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=request.timeout_seconds,
                    check=False,
                )
                duration_ms = int((time.monotonic() - started) * 1000)
                stdout, stdout_truncated = self._truncate(completed.stdout, request.max_output_bytes)
                stderr, stderr_truncated = self._truncate(completed.stderr, request.max_output_bytes)
                artifacts = self._collect_outputs(workspace, request)
                status = "succeeded" if completed.returncode == 0 else "failed"
                error_type = "" if completed.returncode == 0 else "SandboxProcessError"
                return SandboxRun(
                    name=request.name,
                    runtime=runtime_name,
                    command=request.display_command,
                    status=status,
                    exit_code=completed.returncode,
                    timed_out=False,
                    duration_ms=duration_ms,
                    stdout=stdout,
                    stderr=stderr,
                    output_truncated=stdout_truncated or stderr_truncated,
                    artifacts=artifacts,
                    error_type=error_type,
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
            except subprocess.TimeoutExpired as ex:
                duration_ms = int((time.monotonic() - started) * 1000)
                stdout, stdout_truncated = self._truncate(ex.stdout or "", request.max_output_bytes)
                stderr, stderr_truncated = self._truncate(ex.stderr or "", request.max_output_bytes)
                return SandboxRun(
                    name=request.name,
                    runtime=runtime_name,
                    command=request.display_command,
                    status="timed_out",
                    exit_code=None,
                    timed_out=True,
                    duration_ms=duration_ms,
                    stdout=stdout,
                    stderr=stderr,
                    output_truncated=stdout_truncated or stderr_truncated,
                    error_type="TimeoutExpired",
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
            except Exception as ex:  # pylint: disable=broad-except
                return SandboxRun(
                    name=request.name,
                    runtime=runtime_name,
                    command=request.display_command,
                    status="failed",
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stderr=str(ex),
                    error_type=type(ex).__name__,
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )

    def _run_container(self, request: SandboxRequest, decision: FilterDecision) -> SandboxRun:
        if shutil.which("docker") is None:
            if self.allow_local_fallback:
                return self._run_local(request, decision, runtime_name="local-fallback")
            return SandboxRun(
                name=request.name,
                runtime="container",
                command=request.display_command,
                status="failed",
                stderr="docker executable not found",
                error_type="ContainerUnavailable",
                filter_decision=decision,
            )

        started = time.monotonic()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with tempfile.TemporaryDirectory(prefix="code_review_container_") as tmp:
            workspace = Path(tmp)
            self._prepare_workspace(workspace, request)
            container_command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{workspace.resolve()}:/workspace",
                "-w",
                f"/workspace/{request.cwd}",
                "python:3.11-slim",
                *self._resolve_command(request.command, for_container=True),
            ]
            try:
                completed = subprocess.run(
                    container_command,
                    capture_output=True,
                    text=True,
                    timeout=request.timeout_seconds + 5,
                    check=False,
                )
                stdout, stdout_truncated = self._truncate(completed.stdout, request.max_output_bytes)
                stderr, stderr_truncated = self._truncate(completed.stderr, request.max_output_bytes)
                artifacts = self._collect_outputs(workspace, request)
                status = "succeeded" if completed.returncode == 0 else "failed"
                error_type = "" if completed.returncode == 0 else "SandboxProcessError"
                return SandboxRun(
                    name=request.name,
                    runtime="container",
                    command=request.display_command,
                    status=status,
                    exit_code=completed.returncode,
                    timed_out=False,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stdout=stdout,
                    stderr=stderr,
                    output_truncated=stdout_truncated or stderr_truncated,
                    artifacts=artifacts,
                    error_type=error_type,
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
            except subprocess.TimeoutExpired as ex:
                stdout, stdout_truncated = self._truncate(ex.stdout or "", request.max_output_bytes)
                stderr, stderr_truncated = self._truncate(ex.stderr or "", request.max_output_bytes)
                return SandboxRun(
                    name=request.name,
                    runtime="container",
                    command=request.display_command,
                    status="timed_out",
                    timed_out=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stdout=stdout,
                    stderr=stderr,
                    output_truncated=stdout_truncated or stderr_truncated,
                    error_type="TimeoutExpired",
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )

    def _prepare_workspace(self, workspace: Path, request: SandboxRequest) -> None:
        skill_target = workspace / "skills" / "code-review"
        shutil.copytree(self.skill_dir, skill_target)
        for rel_path, content in request.input_files.items():
            target = workspace / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (workspace / "out").mkdir(parents=True, exist_ok=True)
        (workspace / "work" / "inputs").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_command(command: list[str], *, for_container: bool = False) -> list[str]:
        resolved = []
        for part in command:
            if part == "$PYTHON":
                resolved.append("python" if for_container else sys.executable)
            else:
                resolved.append(part)
        return resolved

    @staticmethod
    def _safe_env(extra: dict[str, str]) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key in SAFE_ENV_KEYS}
        for key, value in extra.items():
            if key in SAFE_ENV_KEYS or key.startswith("TRPC_REVIEW_"):
                env[key] = value
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    @staticmethod
    def _truncate(value: str, max_bytes: int) -> tuple[str, bool]:
        redacted, _ = redact_text(value or "")
        encoded = redacted.encode("utf-8", errors="replace")
        if len(encoded) <= max_bytes:
            return redacted, False
        truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
        return truncated + "\n[output truncated]", True

    @staticmethod
    def _collect_outputs(workspace: Path, request: SandboxRequest) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        for rel_path in request.output_files:
            target = workspace / rel_path
            if not target.is_file():
                continue
            content = target.read_text(encoding="utf-8", errors="replace")
            redacted, _ = redact_text(content)
            artifacts[rel_path] = redacted
            try:
                json.loads(redacted)
            except Exception:
                pass
        return artifacts
