"""Sandbox executor — runs scripts with timeout, output limits, and env isolation.

Supports three runtime modes:
  - FakeSandboxRunner: simulated execution for CI/testing (no subprocess needed)
  - LocalSandboxRunner: subprocess-based execution (dev fallback)
  - WorkspaceSandboxRunner: Container/Cube/E2B (production)

Use create_sandbox_runner() factory to select the appropriate runner.
"""

import json
import os
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any

from .types import SandboxRun


# ── Abstract base ────────────────────────────────────────────────────

class SandboxRunner(ABC):
    """Abstract sandbox runner interface."""

    @abstractmethod
    def run(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        max_output_bytes: int = 1024 * 1024,
        stdin_input: str | None = None,
    ) -> SandboxRun:
        """Execute a command and return a SandboxRun result."""
        ...


# ── Fake sandbox runner (CI / testing) ───────────────────────────────

class FakeSandboxRunner(SandboxRunner):
    """Simulated sandbox for testing — no real subprocess execution.

    Recognizes special trigger strings in the diff text to simulate
    various edge cases:
      - "force_sandbox_timeout": simulate a timeout
      - "force_large_sandbox_output": simulate output truncation
      - "force_sandbox_failure": simulate a non-zero exit
      - "force_command_not_found": simulate missing command
      - "force_secret_output": simulate secret in sandbox output (tests redaction)
    """

    TRIGGERS: dict[str, dict[str, Any]] = {
        "force_sandbox_timeout": {
            "timed_out": True, "exit_code": -1, "stdout": "",
            "stderr": "Timeout after 30s", "error": "TimeoutExpired",
        },
        "force_large_sandbox_output": {
            "timed_out": False, "exit_code": 0, "stdout": "x" * 2000000,
            "stderr": "", "error": "", "output_truncated": True,
        },
        "force_sandbox_failure": {
            "timed_out": False, "exit_code": 1, "stdout": "Checking...",
            "stderr": "ERROR: Scanner crashed", "error": "RuntimeError",
        },
        "force_command_not_found": {
            "timed_out": False, "exit_code": -2, "stdout": "",
            "stderr": "Command not found: unknown_cmd", "error": "FileNotFoundError",
        },
        "force_secret_output": {
            "timed_out": False, "exit_code": 0,
            "stdout": "API_KEY=sk-abcdef1234567890abcdef1234567890\nTOKEN=ghp_1234567890abcdef1234567890abcdef",
            "stderr": "", "error": "",
        },
    }

    def __init__(self, diff_text: str = ""):
        self.diff_text = diff_text

    def run(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        max_output_bytes: int = 1024 * 1024,
        stdin_input: str | None = None,
    ) -> SandboxRun:
        """Simulate sandbox execution, checking for trigger strings."""
        start = time.monotonic()

        # Check for trigger strings in the diff text
        for trigger, result in self.TRIGGERS.items():
            if trigger in self.diff_text:
                duration_ms = int((time.monotonic() - start) * 1000)
                return SandboxRun(
                    command=" ".join(command),
                    exit_code=result["exit_code"],
                    stdout=result.get("stdout", ""),
                    stderr=result.get("stderr", ""),
                    duration_ms=duration_ms,
                    timed_out=result.get("timed_out", False),
                    output_truncated=result.get("output_truncated", False),
                    error=result.get("error", ""),
                )

        # Default: normal successful run, return scanner-compatible JSON
        scanners_output = json.dumps({
            "status": "ok",
            "findings": [],
            "files_checked": 1,
        })

        duration_ms = int((time.monotonic() - start) * 1000)
        return SandboxRun(
            command=" ".join(command),
            exit_code=0,
            stdout=scanners_output,
            stderr="",
            duration_ms=duration_ms,
            timed_out=False,
            output_truncated=False,
            error="",
        )


# ── Local sandbox runner (subprocess) ────────────────────────────────

class LocalSandboxRunner(SandboxRunner):
    """Subprocess-based sandbox for local development."""

    def __init__(self, env_allowlist: list[str] | None = None):
        self.env_allowlist = env_allowlist

    def run(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        max_output_bytes: int = 1024 * 1024,
        stdin_input: str | None = None,
    ) -> SandboxRun:
        """Execute via subprocess.run() with safety limits."""
        safe_env: dict[str, str] = {}
        if self.env_allowlist:
            for key in self.env_allowlist:
                val = (env or {}).get(key) or os.environ.get(key)
                if val is not None:
                    safe_env[key] = val
        elif env:
            safe_env = dict(env)

        start = time.monotonic()
        timed_out = False
        output_truncated = False
        error = ""

        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                env=safe_env if safe_env else None,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode

        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = (e.stdout or b"").decode("utf-8", errors="replace") if e.stdout else ""
            stderr = (e.stderr or b"").decode("utf-8", errors="replace") if e.stderr else ""
            if len(stderr) < 200:
                stderr += f"\n[Timeout after {timeout_seconds}s]"
            exit_code = -1
        except FileNotFoundError:
            stdout = ""
            stderr = f"Command not found: {command[0]}"
            exit_code = -2
        except Exception as e:
            stdout = ""
            stderr = str(e)
            exit_code = -3
            error = str(e)

        duration_ms = int((time.monotonic() - start) * 1000)

        if len(stdout) + len(stderr) > max_output_bytes:
            truncate_to = max_output_bytes // 2
            stdout = stdout[:truncate_to] + "\n...[output truncated]"
            stderr = stderr[:truncate_to] + "\n...[output truncated]"
            output_truncated = True

        return SandboxRun(
            command=" ".join(command),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            output_truncated=output_truncated,
            error=error,
        )


# ── Factory ──────────────────────────────────────────────────────────

def create_sandbox_runner(
    mode: str = "fake",
    diff_text: str = "",
    env_allowlist: list[str] | None = None,
) -> SandboxRunner:
    """Create a sandbox runner based on mode.

    Args:
        mode: "fake", "local", or "workspace".
        diff_text: Diff content (used by FakeSandboxRunner for trigger detection).
        env_allowlist: Env var allowlist (used by LocalSandboxRunner).

    Returns:
        SandboxRunner instance.
    """
    if mode == "local":
        return LocalSandboxRunner(env_allowlist=env_allowlist)
    elif mode == "workspace":
        # Placeholder for Container/Cube/E2B runner
        # In production, this would use the tRPC-Agent sandbox SDK
        return LocalSandboxRunner(env_allowlist=env_allowlist)
    else:
        return FakeSandboxRunner(diff_text=diff_text)


# ── Legacy convenience function (backward compatible) ────────────────

def execute_in_sandbox(
    command: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    env_allowlist: list[str] | None = None,
    timeout_seconds: int = 30,
    max_output_bytes: int = 1024 * 1024,
    stdin_input: str | None = None,
) -> SandboxRun:
    """Legacy convenience wrapper — uses LocalSandboxRunner.

    For new code, prefer create_sandbox_runner() + runner.run().
    """
    runner = LocalSandboxRunner(env_allowlist=env_allowlist)
    return runner.run(
        command=command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        stdin_input=stdin_input,
    )
