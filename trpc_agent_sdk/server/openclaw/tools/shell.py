# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# This file is part of tRPC-Agent-Python and is licensed under Apache-2.0.
#
# Portions of this file are derived from HKUDS/nanobot (MIT License):
# https://github.com/HKUDS/nanobot.git
#
# Copyright (c) 2025 nanobot contributors
#
# See the project LICENSE / third-party attribution notices for details.
#
"""Shell execution tool."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any
from typing import List
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

_DESCRIPTION = "Execute a shell command and return its output. Use with caution."

_DEFAULT_DENY_PATTERNS: list[str] = [
    r"\brm\s+-[rf]{1,2}\b",  # rm -r, rm -rf, rm -fr
    r"\bdel\s+/[fq]\b",  # del /f, del /q
    r"\brmdir\s+/s\b",  # rmdir /s
    r"(?:^|[;&|]\s*)format\b",  # format (as standalone command only)
    r"\b(mkfs|diskpart)\b",  # disk operations
    r"\bdd\s+if=",  # dd
    r">\s*/dev/sd",  # write to disk
    r"\b(shutdown|reboot|poweroff)\b",  # system power
    r":\(\)\s*\{.*\};\s*:",  # fork bomb
]


class ExecTool(BaseTool):
    """trpc-claw tool to execute shell commands.

    All parameters are static construction-time config; no per-invocation
    context is needed beyond what the LLM passes as tool arguments.

    Args:
        timeout:               Default timeout in seconds (max capped at 600).
        working_dir:           Default working directory; falls back to cwd.
        deny_patterns:         Regex patterns whose match blocks the command.
        allow_patterns:        When non-empty, only matching commands are allowed.
        restrict_to_workspace: Block absolute paths outside *working_dir*.
        path_append:           Extra PATH entries appended before execution.
        filters_name:          Filter names forwarded to
                               :class:`~trpc_agent_sdk.tools.BaseTool`.
        filters:               Filter instances forwarded to
                               :class:`~trpc_agent_sdk.tools.BaseTool`.
    """

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    def __init__(
        self,
        timeout: int = 60,
        working_dir: Optional[str] = None,
        deny_patterns: Optional[List[str]] = None,
        allow_patterns: Optional[List[str]] = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="exec",
            description=_DESCRIPTION,
            filters_name=filters_name,
            filters=filters,
        )
        self._timeout = timeout
        self._working_dir = working_dir
        self._deny_patterns: List[str] = deny_patterns if deny_patterns is not None else _DEFAULT_DENY_PATTERNS
        self._allow_patterns: List[str] = allow_patterns or []
        self._restrict_to_workspace = restrict_to_workspace
        self._path_append = path_append

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="exec",
            description=_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "command":
                    Schema(
                        type=Type.STRING,
                        description="The shell command to execute",
                    ),
                    "working_dir":
                    Schema(
                        type=Type.STRING,
                        description="Optional working directory for the command",
                    ),
                    "timeout":
                    Schema(
                        type=Type.INTEGER,
                        description=("Timeout in seconds. Increase for long-running commands "
                                     "like compilation or installation (default 60, max 600)."),
                        minimum=1,
                        maximum=600,
                    ),
                },
                required=["command"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        command: str = args.get("command", "")
        working_dir: Optional[str] = args.get("working_dir")
        timeout: Optional[int] = args.get("timeout")

        cwd = working_dir or self._working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        effective_timeout = min(timeout or self._timeout, self._MAX_TIMEOUT)

        env = os.environ.copy()
        if self._path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self._path_append

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {effective_timeout} seconds"

            output_parts: list[str] = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            # Head + tail truncation to preserve both start and end of output
            if len(result) > self._MAX_OUTPUT:
                half = self._MAX_OUTPUT // 2
                result = (result[:half] + f"\n\n... ({len(result) - self._MAX_OUTPUT:,} chars truncated) ...\n\n" +
                          result[-half:])
            return result

        except Exception as e:  # pylint: disable=broad-except
            return f"Error executing command: {e}"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _guard_command(self, command: str, cwd: str) -> Optional[str]:
        """Return an error string if *command* should be blocked, else None."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self._deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self._allow_patterns:
            if not any(re.search(p, lower) for p in self._allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self._restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"
            cwd_path = Path(cwd).resolve()
            for raw in self._extract_absolute_paths(cmd):
                try:
                    p = Path(os.path.expandvars(raw.strip())).expanduser().resolve()
                except Exception:  # pylint: disable=broad-except
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command)
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command)
        return win_paths + posix_paths + home_paths
