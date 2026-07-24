# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox runners for the code review dry-run example."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .filters import redact_text
from .governance import SandboxRequest
from .schemas import ParsedDiff
from .schemas import SandboxPolicy
from .schemas import SandboxRun

_SCRIPT_FILES = {
    "diff_summary": "diff_summary.py",
    "static_rules": "static_rules.py",
}


class FakeSandboxRunner:
    """Deterministic sandbox runner that never executes host commands."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self._policy = policy

    def run_requests(
        self, requests: Sequence[SandboxRequest], parsed_diff: ParsedDiff, diff_text: str = ""
    ) -> list[SandboxRun]:
        """Run all allowed requests deterministically."""
        return [self.run_request(request, parsed_diff) for request in requests]

    def run_request(self, request: SandboxRequest, parsed_diff: ParsedDiff) -> SandboxRun:
        """Run one fake sandbox request."""
        started = time.monotonic()
        stdout = ""
        stderr = ""
        exit_code = 0
        timed_out = False
        error_type = None

        if request.script_name == "diff_summary":
            stdout = f"files={len(parsed_diff.files)} hunks={parsed_diff.hunk_count} changed_lines={parsed_diff.changed_line_count}"
        elif request.script_name == "static_rules":
            stdout = "static_rules completed"
        elif request.script_name == "sandbox_failure_probe":
            exit_code = 2
            stderr = "simulated sandbox failure for fixture coverage"
            error_type = "SandboxCommandFailed"
        elif request.script_name == "timeout_probe":
            timed_out = True
            exit_code = 124
            stderr = "simulated sandbox timeout"
            error_type = "SandboxTimeout"
        else:
            exit_code = 1
            stderr = f"unsupported fake sandbox script: {request.script_name}"
            error_type = "UnsupportedScript"

        stdout_excerpt, stdout_truncated = _cap_output(redact_text(stdout), self._policy.max_output_bytes)
        stderr_excerpt, stderr_truncated = _cap_output(redact_text(stderr), self._policy.max_output_bytes)
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        return SandboxRun(
            id=f"sandbox-{request.script_name}",
            script_name=request.script_name,
            runtime=self._policy.runtime,
            decision="allow",
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout_excerpt=stdout_excerpt,
            stderr_excerpt=stderr_excerpt,
            output_truncated=stdout_truncated or stderr_truncated,
            error_type=error_type,
        )


class ContainerSandboxRunner:
    """Sandbox runner backed by the project's existing Docker ContainerClient."""

    def __init__(self, policy: SandboxPolicy, *, scripts_dir: Path | None = None, image: str = "python:3-slim") -> None:
        self._policy = policy
        self._scripts_dir = scripts_dir or Path(__file__).resolve().parents[1] / "skills" / "code-review" / "scripts"
        self._image = image

    def run_requests(
        self, requests: Sequence[SandboxRequest], parsed_diff: ParsedDiff, diff_text: str = ""
    ) -> list[SandboxRun]:
        """Run allowed requests in Docker containers."""
        return [self.run_request(request, diff_text) for request in requests]

    def run_request(self, request: SandboxRequest, diff_text: str) -> SandboxRun:
        """Run one allowlisted script in a Docker container."""
        started = time.monotonic()
        script_file = _SCRIPT_FILES.get(request.script_name)
        if script_file is None:
            return _build_run(
                request,
                runtime="container",
                started=started,
                exit_code=1,
                stderr=f"no container script is mapped for {request.script_name}",
                error_type="SandboxScriptUnavailable",
                policy=self._policy,
            )

        try:
            return asyncio.run(self._run_request_async(request, script_file, diff_text, started))
        except Exception as exc:  # pylint: disable=broad-except
            return _build_run(
                request,
                runtime="container",
                started=started,
                exit_code=-1,
                stderr=str(exc),
                error_type=exc.__class__.__name__,
                policy=self._policy,
            )

    async def _run_request_async(
        self, request: SandboxRequest, script_file: str, diff_text: str, started: float
    ) -> SandboxRun:
        CommandArgs, ContainerClient, ContainerConfig = _load_container_runtime()
        _redirect_trpc_agent_logs_to_stderr()
        with contextlib.redirect_stdout(sys.stderr):
            client = ContainerClient(
                ContainerConfig(
                    image=self._image,
                    host_config={
                        "Binds": [f"{self._scripts_dir}:/workspace/scripts:ro"],
                        "network_mode": "none",
                        "working_dir": "/workspace",
                    },
                )
            )
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = await client.exec_run(
                    ["python3", f"/workspace/scripts/{script_file}"],
                    CommandArgs(timeout=self._policy.timeout_seconds, stdin=diff_text, environment={}),
                )
        finally:
            cleanup = getattr(client, "_cleanup_container", None)
            if callable(cleanup):
                cleanup()

        error_type = None
        if result.is_timeout:
            error_type = "SandboxTimeout"
        elif result.exit_code:
            error_type = "SandboxCommandFailed"
        return _build_run(
            request,
            runtime="container",
            started=started,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.is_timeout,
            error_type=error_type,
            policy=self._policy,
        )



def _redirect_trpc_agent_logs_to_stderr() -> None:
    logging.getLogger("trpc_agent_sdk").disabled = True


def _load_container_runtime():
    try:
        from trpc_agent_sdk.code_executors.container import CommandArgs
        from trpc_agent_sdk.code_executors.container import ContainerClient
        from trpc_agent_sdk.code_executors.container import ContainerConfig
    except ModuleNotFoundError:
        repo_root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(repo_root))
        from trpc_agent_sdk.code_executors.container import CommandArgs
        from trpc_agent_sdk.code_executors.container import ContainerClient
        from trpc_agent_sdk.code_executors.container import ContainerConfig
    return CommandArgs, ContainerClient, ContainerConfig


def create_sandbox_runner(policy: SandboxPolicy, *, container_image: str = "python:3-slim") -> FakeSandboxRunner | ContainerSandboxRunner:
    """Create a sandbox runner for the configured runtime."""
    if policy.runtime == "container":
        return ContainerSandboxRunner(policy, image=container_image)
    return FakeSandboxRunner(policy)


def _build_run(
    request: SandboxRequest,
    *,
    runtime: str,
    started: float,
    exit_code: int | None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    error_type: str | None = None,
    policy: SandboxPolicy,
) -> SandboxRun:
    stdout_excerpt, stdout_truncated = _cap_output(redact_text(stdout), policy.max_output_bytes)
    stderr_excerpt, stderr_truncated = _cap_output(redact_text(stderr), policy.max_output_bytes)
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    return SandboxRun(
        id=f"sandbox-{request.script_name}",
        script_name=request.script_name,
        runtime=runtime,
        decision="allow",
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
        stdout_excerpt=stdout_excerpt,
        stderr_excerpt=stderr_excerpt,
        output_truncated=stdout_truncated or stderr_truncated,
        error_type=error_type,
    )


def _cap_output(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    capped = raw[:max_bytes].decode("utf-8", errors="ignore")
    return capped, True
