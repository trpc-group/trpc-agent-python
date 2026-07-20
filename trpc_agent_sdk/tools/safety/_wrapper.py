# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standalone wrapper for tool script safety scanning.

This module provides the SafetyWrapper class which can be used to wrap
tool execution with safety checks without modifying the Filter chain.
This is useful for quick integration or when the Filter system is not
being used.

Usage::

    from trpc_agent_sdk.tools.safety import SafetyWrapper

    wrapper = SafetyWrapper()

    # Wrap a tool execution
    result = await wrapper.run_safe(
        tool_name="Bash",
        script_content="rm -rf /",
        script_type="bash",
        execute_fn=lambda: run_bash_command("rm -rf /"),
    )
    # result["blocked"] == True
    # result["report"] contains the SafetyReport
"""

from __future__ import annotations

import os
from typing import Any
from typing import Callable
from typing import Optional

from trpc_agent_sdk.log import logger

from ._audit import AuditLogger
from ._policy import SafetyPolicy
from ._scanner import SafetyScanner
from ._types import SafetyReport
from ._types import ScanInput
from ._types import ScriptType
from ._types import SafetyDecision

_DEFAULT_POLICY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "tool_safety_policy.yaml",
)


class SafetyWrapper:
    """Standalone wrapper for tool script safety scanning.

    This wrapper can be used outside the Filter system to perform
    safety checks before executing scripts. It provides a simple
    ``run_safe`` method that wraps an execution function.

    The wrapper is **not** a filter — it is a standalone utility
    for users who want to integrate safety checks manually.
    """

    def __init__(
        self,
        policy_path: Optional[str] = None,
        policy: Optional[SafetyPolicy] = None,
    ) -> None:
        """Initialize the safety wrapper.

        Args:
            policy_path: Path to the ``tool_safety_policy.yaml`` file.
                Defaults to the project root.
            policy: An already-loaded SafetyPolicy instance. If provided,
                ``policy_path`` is ignored.
        """
        if policy is not None:
            self._policy = policy
        else:
            path = policy_path or _DEFAULT_POLICY_PATH
            if os.path.exists(path):
                self._policy = SafetyPolicy.from_file(path)
            else:
                self._policy = SafetyPolicy()
        self._scanner = SafetyScanner(self._policy)
        self._audit_logger = AuditLogger()

    async def run_safe(
        self,
        tool_name: str,
        script_content: str,
        script_type: str = "auto",
        execute_fn: Optional[Callable[[], Any]] = None,
        command_line_args: Optional[list[str]] = None,
        working_directory: Optional[str] = None,
        env_vars: Optional[dict[str, str]] = None,
        tool_metadata: Optional[dict[str, str]] = None,
    ) -> dict:
        """Execute a script with safety scanning.

        Scans the script content for security risks before execution.
        If the script is blocked, the execution function is not called.

        Args:
            tool_name: Name of the tool (for audit logging).
            script_content: The script content to scan and execute.
            script_type: Type of script (``"bash"``, ``"python"``, or
                ``"auto"`` for automatic detection).
            execute_fn: Async callable that executes the script.
                If not provided, only the scan is performed.
            command_line_args: Optional command-line arguments.
            working_directory: Optional working directory.
            env_vars: Optional environment variables.
            tool_metadata: Optional tool metadata dict.

        Returns:
            A dict with keys:
            - ``"blocked"``: Whether execution was blocked.
            - ``"report"``: The SafetyReport dict.
            - ``"result"``: The result of ``execute_fn`` (if called).
            - ``"error"``: Error message if blocked.
        """
        # Parse script type
        st = self._parse_script_type(script_type)

        # Build scan input
        scan_input = ScanInput(
            script_content=script_content,
            script_type=st,
            command_line_args=command_line_args,
            working_directory=working_directory,
            env_vars=env_vars,
            tool_name=tool_name,
            tool_metadata=tool_metadata,
        )

        # Scan
        report = self._scanner.scan(scan_input)

        # Audit
        self._audit_logger.log_report(report)

        # Check if blocked
        if report.is_blocked:
            rule_ids = ",".join(m.rule_id for m in report.matches)
            msg = (f"Tool '{tool_name}' execution blocked: "
                   f"decision={report.decision.name}, "
                   f"risk={report.risk_level.name}, "
                   f"rules=[{rule_ids}]")
            logger.warning("SafetyWrapper: %s", msg)
            return {
                "blocked": True,
                "report": report.to_dict(),
                "result": None,
                "error": msg,
            }

        # Execute if function provided
        result = None
        if execute_fn is not None:
            result = await execute_fn()

        return {
            "blocked": False,
            "report": report.to_dict(),
            "result": result,
            "error": None,
        }

    def scan_only(
        self,
        tool_name: str,
        script_content: str,
        script_type: str = "auto",
    ) -> dict:
        """Scan a script without executing it.

        This is a synchronous convenience method for quick safety checks.

        Args:
            tool_name: Name of the tool.
            script_content: The script content to scan.
            script_type: Type of script (``"bash"``, ``"python"``, ``"auto"``).

        Returns:
            The SafetyReport dict.
        """
        import asyncio  # noqa: E811
        st = self._parse_script_type(script_type)
        scan_input = ScanInput(
            script_content=script_content,
            script_type=st,
            tool_name=tool_name,
        )
        report = self._scanner.scan(scan_input)
        self._audit_logger.log_report(report)
        return report.to_dict()

    @staticmethod
    def _parse_script_type(script_type: str) -> ScriptType:
        """Parse a string script type into a ScriptType enum."""
        mapping = {
            "bash": ScriptType.BASH,
            "sh": ScriptType.BASH,
            "shell": ScriptType.BASH,
            "python": ScriptType.PYTHON,
            "py": ScriptType.PYTHON,
            "auto": ScriptType.UNKNOWN,
        }
        return mapping.get(script_type.lower(), ScriptType.UNKNOWN)
