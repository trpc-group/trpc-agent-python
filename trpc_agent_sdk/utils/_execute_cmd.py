# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Execute Command Utility Module.

This module provides a utility function to execute a command and return its output.
"""

import asyncio
from collections import namedtuple
import os
from pathlib import Path
import signal
import subprocess
from typing import Dict
from typing import Optional

CommandExecResult = namedtuple("CommandExecResult", ["stdout", "stderr", "exit_code", "is_timeout"])
_TERMINATE_GRACE_SECONDS = 0.5


async def _stop_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess group, escalate, and always reap the leader."""

    if process.returncode is not None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return

    def _signal_group(sig: signal.Signals) -> None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, sig)
                return
            except ProcessLookupError:
                return
            except OSError:
                pass
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    _signal_group(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        # The leader can exit while a descendant ignores SIGTERM. The process
        # group remains addressable by its original id until all members exit.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    except asyncio.TimeoutError:
        pass
    _signal_group(signal.SIGKILL)
    await process.wait()


async def async_execute_command(
    work_dir: Path,
    cmd_args: list[str],
    input: Optional[bytes] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> CommandExecResult:
    """Execute a command and return its output.

    Args:
        work_dir: Working directory
        cmd_args: Command arguments
        input: Standard input
        env: Environment variables
        timeout: Timeout in seconds
    Returns:
        Command execution result

    Raises:
        subprocess.TimeoutExpired: If execution times out
        subprocess.CalledProcessError: If execution fails
    """
    process: asyncio.subprocess.Process | None = None
    try:
        # Create async subprocess using create_subprocess_exec for argument list
        # create_subprocess_exec expects individual arguments, not a shell command string
        process_options = {}
        if os.name == "posix":
            # A separate session lets timeout/cancellation clean up descendants as a group.
            process_options["start_new_session"] = True
        elif os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if input else None,
            env=env or {},
            **process_options,
        )

        co = process.communicate(input=input)
        # Execute with timeout if specified
        if timeout:
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(co, timeout=timeout)
            except asyncio.TimeoutError:
                await _stop_process_tree(process)
                return CommandExecResult(
                    stdout="",
                    stderr=f"Command timed out after {timeout}s: {' '.join(cmd_args)} in {work_dir}",
                    exit_code=-1,
                    is_timeout=True,
                )
        else:
            # No timeout, wait for completion
            stdout_bytes, stderr_bytes = await co

        # Decode output to string (function always returns str)
        stdout_text = stdout_bytes.decode("utf-8") if stdout_bytes else ""
        stderr_text = stderr_bytes.decode("utf-8") if stderr_bytes else ""
        # Check return code (check=True equivalent)
        if process.returncode != 0:
            return CommandExecResult(
                stdout="",
                stderr=f"command failed (cwd={work_dir}, cmd={' '.join(cmd_args)}): {stderr_text}",
                exit_code=process.returncode,
                is_timeout=False,
            )
    except asyncio.CancelledError:
        if process is not None:
            await _stop_process_tree(process)
        raise
    except Exception as ex:  # pylint: disable=broad-except
        if process is not None:
            await _stop_process_tree(process)
        return CommandExecResult(
            stdout="",
            stderr=f"command execution error (cwd={work_dir}, "
            f"cmd={' '.join(cmd_args)}): {str(ex)}",
            exit_code=-1,
            is_timeout=False,
        )

    else:
        return CommandExecResult(stdout=stdout_text, stderr=stderr_text, exit_code=process.returncode, is_timeout=False)
