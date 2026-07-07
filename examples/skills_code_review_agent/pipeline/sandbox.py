"""Sandbox executor — runs scripts with timeout, output limits, and env isolation.

Uses subprocess for local execution (fallback) and is designed to work
with Container/Cube code executors when available.
"""

import os
import subprocess
import time

from .types import SandboxRun


def execute_in_sandbox(
    command: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    env_allowlist: list[str] | None = None,
    timeout_seconds: int = 30,
    max_output_bytes: int = 1024 * 1024,
    stdin_input: str | None = None,
) -> SandboxRun:
    """Execute a command in a local sandbox with safety limits.

    Args:
        command: Command and arguments as a list.
        cwd: Working directory for execution.
        env: Environment variables to pass.
        env_allowlist: If set, only these env vars are passed through.
        timeout_seconds: Maximum execution time.
        max_output_bytes: Maximum combined stdout+stderr size.
        stdin_input: Optional input to pipe to stdin.

    Returns:
        SandboxRun with execution results.
    """
    # Build safe environment
    safe_env: dict[str, str] = {}
    if env_allowlist:
        for key in env_allowlist:
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

    # Truncate output if too large
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
