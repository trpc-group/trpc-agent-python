# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import ToolScriptScanRequest
from trpc_agent_sdk.tools.safety import write_audit_event
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type


def _extract_base_commands(command: str) -> Optional[list[str]]:
    """Extract every executable command segment without executing the shell."""
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    index = 0
    previous_is_redirect = False

    while index < len(command):
        char = command[index]

        if escaped:
            current.append(char)
            escaped = False
            previous_is_redirect = False
            index += 1
            continue

        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            previous_is_redirect = False
            index += 1
            continue

        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            elif quote == '"' and (char == "`" or command.startswith("$(", index)):
                return None
            previous_is_redirect = False
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            previous_is_redirect = False
            index += 1
            continue

        if char == "`" or command.startswith(("$(", "<(", ">("), index):
            return None

        if char in {"|", ";", "&", "\n"}:
            # Keep file-descriptor redirections such as 2>&1 and &>file in
            # the current segment; a standalone ampersand is a separator.
            next_is_redirect = index + 1 < len(command) and command[index + 1] == ">"
            is_redirection = char == "&" and (previous_is_redirect or next_is_redirect)
            if is_redirection:
                current.append(char)
                previous_is_redirect = False
                index += 1
                continue

            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            previous_is_redirect = False
            if command.startswith(("||", "&&", "|&", ";;"), index):
                index += 2
            else:
                index += 1
            continue

        current.append(char)
        previous_is_redirect = char in {"<", ">"}
        index += 1

    if quote or escaped:
        return None

    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    if not segments:
        return None

    base_commands: list[str] = []
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return None
        if not tokens:
            return None
        base_commands.append(tokens[0])
    return base_commands


class BashTool(BaseTool):
    """Tool for executing bash commands."""

    # Whitelist of commands allowed outside working directory
    ALLOWED_COMMANDS_OUTSIDE_WORKDIR = ["ls", "pwd", "cat", "grep", "find", "head", "tail", "wc", "echo"]

    def __init__(
        self,
        cwd: Optional[str] = None,
        whitelist_commands: Optional[list[str]] = None,
        safety_scanner: Optional[ToolScriptSafetyScanner] = None,
        safety_audit_log_path: Optional[str] = None,
        enable_safety_guard: bool = False,
        block_on_review: bool = False,
    ):
        super().__init__(
            name="Bash",
            description=("Execute bash command in shell. Returns stdout, stderr, return_code. "
                         "Supports timeout (default 300s) and security restrictions "
                         "(whitelist for commands outside working directory)."),
        )
        self.cwd = cwd or os.getcwd()
        self.whitelist_commands = whitelist_commands
        self.safety_scanner = safety_scanner or (ToolScriptSafetyScanner() if enable_safety_guard else None)
        self.safety_audit_log_path = safety_audit_log_path
        self.enable_safety_guard = enable_safety_guard
        self.block_on_review = block_on_review

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
        if self.whitelist_commands is not None:
            allowed_commands = self.whitelist_commands
        else:
            try:
                Path(execution_dir).resolve().relative_to(Path(self.cwd).resolve())
                return True
            except ValueError:
                allowed_commands = self.ALLOWED_COMMANDS_OUTSIDE_WORKDIR

        base_commands = _extract_base_commands(command)
        if not base_commands:
            return False

        return all(base_command in allowed_commands for base_command in base_commands)

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        command = args.get("command")
        cwd = args.get("cwd")
        timeout = args.get("timeout", 300)

        if not command:
            return {"error": "INVALID_PARAMETER: command parameter is required"}

        try:
            execution_dir = self._resolve_execution_directory(cwd)

            safety_report = None
            if self.enable_safety_guard:
                safety_report = self.safety_scanner.scan(
                    ToolScriptScanRequest(
                        script=command,
                        language="bash",
                        cwd=execution_dir,
                        env=os.environ.copy(),
                        tool_name=self.name,
                        tool_metadata={
                            "timeout": timeout,
                        },
                    ))
                should_block = safety_report.decision == Decision.DENY or (
                    self.block_on_review and safety_report.decision == Decision.NEEDS_HUMAN_REVIEW)
                safety_report.set_blocked(should_block)
                if self.safety_audit_log_path:
                    write_audit_event(self.safety_audit_log_path, safety_report)
                if should_block:
                    return {
                        "success": False,
                        "error": f"TOOL_SAFETY_BLOCKED: {safety_report.summary}",
                        "command": command,
                        "return_code": -1,
                        "safety_report": safety_report.to_dict(),
                    }

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
                "safety_report": safety_report.to_dict() if safety_report else None,
            }
        except Exception as ex:  # pylint: disable=broad-except
            return {
                "success": False,
                "error": f"EXECUTION_ERROR: unexpected error occurred during command execution: {str(ex)}",
                "command": command,
            }
