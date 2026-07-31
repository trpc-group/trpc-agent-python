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
            # Docker daemon unavailable → graceful degradation to local
            if self._docker_unavailable(r):
                r = self._run_local(script_path, args, stdin_input, effective_timeout)
                r["_fallback"] = "container→local (Docker unavailable)"
        elif self.sandbox_type == "cube":
            r = self._run_cube(script_path, args, stdin_input, effective_timeout)
        else:
            r = self._run_local(script_path, args, stdin_input, effective_timeout)

        result.update(r)
        self._elapsed_ms += result.get("duration_ms", 0)

        budget_ok = self._elapsed_ms / 1000 < self.budget_seconds
        if result.get("timed_out") and not budget_ok:
            result["budget_exceeded"] = True
        return result

    @staticmethod
    def _docker_unavailable(result: dict[str, Any]) -> bool:
        """Detect if Docker is unreachable (daemon down OR binary missing)."""
        stderr = result.get("stderr", "").lower()
        if result.get("exit_code") not in (-1, 1):
            return False
        return ("connect to the docker" in stderr
                or "cannot connect to the docker" in stderr
                or "docker daemon" in stderr
                or "pipe/docker_engine" in stderr
                or "file not found" in stderr
                or "command not found" in stderr
                or "is the daemon running" in stderr
                or "docker" in stderr and "not recognized" in stderr)

    def _run_docker(self, script_path: str, args: list[str] | None,
                    stdin_input: str | None, timeout: float) -> dict[str, Any]:
        """Run script in an isolated Docker container.

        Uses the GNU timeout command to enforce the per-script timeout inside the
        container. Diff data is passed via stdin to avoid needing to mount host paths.
        """
        args = args or []
        start = time.time()
        script_name = Path(script_path).name
        script_dir = str(Path(script_path).parent)

        cmd = [
            "docker", "run", "--rm",
            "--network=none",
            f"--memory={self.memory_mb}m",
            "-v", f"{script_dir}:/scripts:ro",
            "python:3.12-slim",
            "timeout", str(int(timeout)),
            "python", f"/scripts/{script_name}",
        ] + args

        result = self._exec(cmd, stdin_input, start, timeout)
        result["script"] = script_path
        return result

    def _run_cube(self, script_path: str, args: list[str] | None,
                  stdin_input: str | None, timeout: float) -> dict[str, Any]:
        """Run script in a remote Cube/E2B sandbox via the framework client.

        Requires the optional ``[cube]`` extra and Cube/E2B credentials
        (E2B_API_URL / E2B_API_KEY / E2B_TEMPLATE). Raises RuntimeError with a
        clear message when unavailable so the caller can fail loudly instead of
        silently degrading to local execution.
        """
        import asyncio
        import shlex

        async def _go() -> dict[str, Any]:
            from trpc_agent_sdk.code_executors.cube import create_cube_sandbox_client
            from trpc_agent_sdk.code_executors.cube._types import CubeClientConfig

            cfg = CubeClientConfig()
            try:
                cfg.resolve_api_url()
                cfg.resolve_api_key()
                cfg.resolve_template()
            except ValueError as exc:
                raise RuntimeError(f"cube backend unavailable: {exc}") from exc

            client = await create_cube_sandbox_client(cfg)
            start = time.time()
            try:
                remote_dir = f"/tmp/cr_{time.time_ns()}"
                await client.commands_run(f"mkdir -p {remote_dir}")
                await client.upload_path(Path(script_path).parent, remote_dir)
                name = Path(script_path).name
                cmd = f"python {remote_dir}/{name}"
                if args:
                    cmd += " " + " ".join(shlex.quote(str(a)) for a in args)
                res = await client.commands_run(
                    cmd,
                    stdin=stdin_input.encode("utf-8") if stdin_input else None,
                    timeout=timeout,
                )
                return {
                    "exit_code": res.exit_code,
                    "stdout": res.stdout[: self.max_output],
                    "stderr": res.stderr[: self.max_output],
                    "timed_out": res.timed_out,
                    "exception_type": "TimeoutExpired" if res.timed_out else None,
                    "duration_ms": int((time.time() - start) * 1000),
                    "_fallback": "cube",
                }
            finally:
                try:
                    await client.destroy()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass

        try:
            return asyncio.run(_go())
        except RuntimeError:
            raise
        except ImportError as exc:
            raise RuntimeError(
                "cube backend requires the optional extra: pip install trpc-agent-py[cube]"
            ) from exc

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
            # Docker timeout command exits with 124 when the timeout fires
            if proc.returncode == 124 and self.sandbox_type == "container":
                result["timed_out"] = True
                result["exception_type"] = "TimeoutExpired"
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
