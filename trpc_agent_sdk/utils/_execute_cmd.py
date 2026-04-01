# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Execute Command Utility Module.

This module provides a utility function to execute a command and return its output.
"""

import asyncio
from collections import namedtuple
from pathlib import Path
from typing import Dict
from typing import Optional

CommandExecResult = namedtuple('CommandExecResult', ['stdout', 'stderr', 'exit_code', 'is_timeout'])


async def async_execute_command(work_dir: Path,
                                cmd_args: list[str],
                                input: Optional[bytes] = None,
                                env: Optional[Dict[str, str]] = None,
                                timeout: Optional[float] = None) -> CommandExecResult:
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
    try:
        # Create async subprocess using create_subprocess_exec for argument list
        # create_subprocess_exec expects individual arguments, not a shell command string
        process = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if input else None,
            env=env or {},
        )

        co = process.communicate(input=input)
        # Execute with timeout if specified
        if timeout:
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(co, timeout=timeout)
            except asyncio.TimeoutError:
                process.terminate()
                await process.wait()
                # Kill the process if timeout
                return CommandExecResult(
                    stdout="",
                    stderr=f"Command timed out after {timeout}s: {' '.join(cmd_args)} in {work_dir}",
                    exit_code=-1,
                    is_timeout=True)
        else:
            # No timeout, wait for completion
            stdout_bytes, stderr_bytes = await co

        # Decode output to string (function always returns str)
        stdout_text = stdout_bytes.decode('utf-8') if stdout_bytes else ""
        stderr_text = stderr_bytes.decode('utf-8') if stderr_bytes else ""
        # Check return code (check=True equivalent)
        if process.returncode != 0:
            return CommandExecResult(stdout="",
                                     stderr=f"command failed (cwd={work_dir}, cmd={' '.join(cmd_args)}): {stderr_text}",
                                     exit_code=process.returncode,
                                     is_timeout=False)
    except Exception as ex:  # pylint: disable=broad-except
        return CommandExecResult(stdout="",
                                 stderr=f"command execution error (cwd={work_dir}, "
                                 f"cmd={' '.join(cmd_args)}): {str(ex)}",
                                 exit_code=-1,
                                 is_timeout=False)

    else:
        return CommandExecResult(stdout=stdout_text, stderr=stderr_text, exit_code=process.returncode, is_timeout=False)
