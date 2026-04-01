# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Bash command execution tool implementation.

This module provides the BashTool class which enables agents to execute bash
commands with timeout and security restrictions.
"""

import asyncio
import os
import shlex
from pathlib import Path
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type


class BashTool(BaseTool):
    """Tool for executing bash commands."""

    # Whitelist of commands allowed outside working directory
    ALLOWED_COMMANDS_OUTSIDE_WORKDIR = ["ls", "pwd", "cat", "grep", "find", "head", "tail", "wc", "echo"]

    def __init__(self, cwd: Optional[str] = None, whitelist_commands: Optional[list[str]] = None):
        super().__init__(
            name="Bash",
            description=("Execute bash command in shell. Returns stdout, stderr, return_code. "
                         "Supports timeout (default 300s) and security restrictions "
                         "(whitelist for commands outside working directory)."),
        )
        self.cwd = cwd or os.getcwd()
        self.whitelist_commands = whitelist_commands

    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="Bash",
            description=("Execute bash command in shell. Returns stdout, stderr, return_code. "
                         "Use when: running system commands, building projects, "
                         "running tests, checking git status, or any shell operations. "
                         "Supports: pipes, redirections, complex commands, timeout control. "
                         "Security: Commands in working directory have no restrictions. "
                         "Commands outside working directory limited to whitelist: "
                         "ls, pwd, cat, grep, find, head, tail, wc, echo. "
                         "Timeout: Default 300s (5min). Increase for long-running "
                         "commands (builds, tests). "
                         "Example: Bash(command='git status', cwd='src/', timeout=60) "
                         "checks git status in src directory."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "command":
                    Schema(
                        type=Type.STRING,
                        description=("Bash command to execute. Can include pipes, redirections, complex commands. "
                                     "Example: 'ls -la', 'git status', "
                                     "'python -m pytest', 'grep -r pattern src/', "
                                     "'find . -name \"*.py\" | head -10'."),
                    ),
                    "cwd":
                    Schema(
                        type=Type.STRING,
                        description=("Optional. Working directory for command execution. "
                                     "Relative paths resolved from tool's default cwd. "
                                     "Default: tool's cwd. "
                                     "Example: 'src/' runs in src directory, '/tmp' uses absolute path."),
                    ),
                    "timeout":
                    Schema(
                        type=Type.INTEGER,
                        description=("Optional. Timeout in seconds. Default: 300 (5 minutes). "
                                     "Command terminated if exceeds timeout. "
                                     "Increase for long-running commands (builds, tests)."),
                    ),
                },
                required=["command"],
            ),
        )

    def _resolve_execution_directory(self, cwd: Optional[str]) -> str:
        """Resolve execution directory.

        Args:
            cwd: User-specified working directory

        Returns:
            Resolved absolute path
        """
        if cwd is None:
            return self.cwd

        cwd_path = Path(cwd)
        if not cwd_path.is_absolute():
            return str(Path(self.cwd) / cwd_path)
        return str(cwd_path)

    def _is_command_safe(self, command: str, execution_dir: str) -> bool:
        """Check if command is safe to execute.

        Args:
            command: Command to execute
            execution_dir: Execution directory

        Returns:
            Whether it's safe to execute
        """
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="|")
            lexer.whitespace_split = True
            tokens = list(lexer)

            base_commands = []
            current_command = []
            for token in tokens:
                if token == "|":
                    if current_command:
                        base_commands.append(current_command[0])
                        current_command = []
                else:
                    if not current_command:
                        current_command.append(token)
        except Exception:  # pylint: disable=broad-except
            base_commands = [command.split()[0] if command.split() else ""]

        for base_command in base_commands:
            if self.whitelist_commands is not None:
                if base_command not in self.whitelist_commands:
                    return False
            else:
                try:
                    Path(execution_dir).resolve().relative_to(Path(self.cwd).resolve())
                except ValueError:
                    if base_command not in self.ALLOWED_COMMANDS_OUTSIDE_WORKDIR:
                        return False

        return True

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        command = args.get("command")
        cwd = args.get("cwd")
        timeout = args.get("timeout", 300)

        if not command:
            return {"error": "INVALID_PARAMETER: command parameter is required"}

        try:
            execution_dir = self._resolve_execution_directory(cwd)

            if not self._is_command_safe(command, execution_dir):
                if self.whitelist_commands is not None:
                    allowed_commands = ", ".join(self.whitelist_commands)
                    error_msg = (f"SECURITY_RESTRICTION: only whitelisted commands allowed. "
                                 f"Allowed commands: {allowed_commands}")
                else:
                    error_msg = (f"SECURITY_RESTRICTION: only whitelisted commands allowed "
                                 f"outside working directory. Current directory: {execution_dir}")

                return {
                    "success": False,
                    "error": error_msg,
                    "command": command,
                    "return_code": -1,
                }

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=execution_dir,
                env=os.environ.copy(),
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {
                    "success": False,
                    "error": f"COMMAND_TIMEOUT: command timed out after {timeout} seconds",
                    "command": command,
                    "return_code": -1,
                }

            stdout_text = stdout.decode("utf-8", errors="ignore")
            stderr_text = stderr.decode("utf-8", errors="ignore")
            return_code = process.returncode

            texts_parts = [f"Command: {command}"]
            texts_parts.append(f"Working directory: {execution_dir}")
            texts_parts.append(f"Return code: {return_code}")

            if stdout_text:
                texts_parts.append(f"Stdout:\n{stdout_text}")
            if stderr_text:
                texts_parts.append(f"Stderr:\n{stderr_text}")

            return {
                "success": return_code == 0,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "return_code": return_code,
                "command": command,
                "cwd": execution_dir,
                "formatted_output": "\n".join(texts_parts),
            }
        except Exception as ex:  # pylint: disable=broad-except
            return {
                "success": False,
                "error": f"EXECUTION_ERROR: unexpected error occurred during command execution: {str(ex)}",
                "command": command,
            }
