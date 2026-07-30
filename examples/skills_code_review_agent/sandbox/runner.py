# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Sandbox execution wrapper with Docker container and local subprocess backends.

Uses Docker containers as the production sandbox (network-none, memory-limited).
Local subprocess is available as a development fallback.
"""

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 100
DEFAULT_MEMORY_MB = 512
DEFAULT_BUDGET_SECONDS = 300
DEFAULT_ALLOWED_ENV = r"^(PATH|PYTHON.*|HOME|TEMP|TMP|USER|USERNAME|SYSTEMROOT|LANG|LC_.*)$"


class SandboxRunner:
    """Wraps Docker container and local subprocess script execution.

    container — Docker with network isolation, memory limits, env filtering.
    local     — subprocess with timeout/output limits, env whitelist.

    Features:
    - Per-script timeout (default 30s)
    - Global budget (default 300s) — cumulates across all scripts
    - Output size limit (default 100KB)
    - Environment variable whitelist (PATH, PYTHON*, HOME, etc.)
    - Forbidden path filtering (managed by agent/filter.py)
    """

    def __init__(self, sandbox_type: str = "local", **kwargs):
        self.sandbox_type = sandbox_type
        self.timeout = kwargs.get("timeout", DEFAULT_TIMEOUT_SECONDS)
        self.max_output = kwargs.get("max_output", DEFAULT_MAX_OUTPUT_BYTES)
        self.memory_mb = kwargs.get("memory_mb", DEFAULT_MEMORY_MB)
        self.budget_seconds = kwargs.get("budget_seconds", DEFAULT_BUDGET_SECONDS)
        self.allowed_env_re = re.compile(
            kwargs.get("allowed_env_pattern", DEFAULT_ALLOWED_ENV))
        self._elapsed_ms = 0

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed_ms / 1000.0

    @property
    def budget_remaining_seconds(self) -> float:
        return max(0, self.budget_seconds - self.elapsed_seconds)

    def _filter_env(self) -> dict[str, str]:
        """Return environment dict with only whitelisted variables."""
        return {
            k: v for k, v in os.environ.items()
            if self.allowed_env_re.match(k)
        }

    def run_script(self, script_path: str, args: list[str] | None = None,
                   stdin_input: str | None = None) -> dict[str, Any]:
        """Run a script with timeout, output limits, env whitelist, and budget.

        If the remaining budget is less than the per-script timeout, the
        timeout is capped to the remaining budget to stay within limits.

        Returns: {script, exit_code, stdout, stderr, timed_out, duration_ms, budget_exceeded}
        """
        if self.elapsed_seconds >= self.budget_seconds:
            return {
                "script": script_path, "exit_code": -1,
                "stdout": "", "stderr": f"Global budget exceeded ({self.budget_seconds}s)",
                "timed_out": False, "duration_ms": 0, "budget_exceeded": True,
            }

        effective_timeout = min(self.timeout, self.budget_remaining_seconds)
        result = {"script": script_path, "budget_exceeded": False}

        if self.sandbox_type == "container":
            r = self._run_docker(script_path, args, stdin_input, effective_timeout)
        else:
            r = self._run_local(script_path, args, stdin_input, effective_timeout)

        result.update(r)
        self._elapsed_ms += result.get("duration_ms", 0)

        budget_ok = self._elapsed_ms / 1000 < self.budget_seconds
        if result.get("timed_out") and not budget_ok:
            result["budget_exceeded"] = True
        return result

    def _run_docker(self, script_path: str, args: list[str] | None,
                    stdin_input: str | None, timeout: float) -> dict[str, Any]:
        """Run script in an isolated Docker container."""
        args = args or []
        start = time.time()
        script_name = Path(script_path).name
        script_dir = str(Path(script_path).parent)

        cmd = [
            "docker", "run", "--rm",
            "--network=none",
            f"--memory={self.memory_mb}m",
            "-v", f"{script_dir}:/scripts:ro",
            f"--timeout={int(timeout)}",
            "python:3.12-slim",
            "python", f"/scripts/{script_name}",
        ] + args

        result = self._exec(cmd, stdin_input, start, timeout)
        result["script"] = script_path
        return result

    def _run_local(self, script_path: str, args: list[str] | None,
                   stdin_input: str | None, timeout: float) -> dict[str, Any]:
        """Run script via subprocess (development fallback)."""
        args = args or []
        start = time.time()
        cmd = ["python", script_path] + args
        result = self._exec(cmd, stdin_input, start, timeout)
        result["script"] = script_path
        return result

    def _exec(self, cmd: list[str], stdin_input: str | None,
              start: float, timeout: float) -> dict[str, Any]:
        """Execute a command with env whitelist and return structured result."""
        result: dict[str, Any] = {
            "script": "",
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "duration_ms": 0,
            "exception_type": None,
        }
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, input=stdin_input,
                env=self._filter_env(),
            )
            result["exit_code"] = proc.returncode
            result["stdout"] = proc.stdout[:self.max_output]
            result["stderr"] = proc.stderr[:self.max_output]
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            result["exception_type"] = "TimeoutExpired"
            result["stderr"] = f"Sandbox execution timed out after {timeout:.1f}s"
        except FileNotFoundError:
            result["exception_type"] = "FileNotFoundError"
            result["stderr"] = f"Sandbox error: command not found ({cmd[0]})"
        except Exception as e:
            result["exception_type"] = type(e).__name__
            result["stderr"] = f"Sandbox execution error: {str(e)}"

        result["duration_ms"] = int((time.time() - start) * 1000)
        return result
