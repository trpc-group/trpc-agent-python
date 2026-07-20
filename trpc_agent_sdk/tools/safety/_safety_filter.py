# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SafetyFilter — Tool execution safety filter for the TRPC Agent framework.

This module provides the SafetyFilter class which integrates with the
existing Filter system to perform script safety scanning before tool
execution. It is registered as a ``FilterType.TOOL`` filter.

Usage::

    from trpc_agent_sdk.tools.safety import SafetyFilter

    # The filter is auto-registered with the name "safety_filter".
    # To enable it on a tool:

    tool = BashTool(filters_name=["safety_filter"])
"""

from __future__ import annotations

import os
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.log import logger

from ._audit import AuditLogger
from ._policy import SafetyPolicy
from ._scanner import SafetyScanner
from ._types import SafetyDecision
from ._types import SafetyReport
from ._types import ScanInput
from ._types import ScriptType

_DEFAULT_POLICY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "tool_safety_policy.yaml",
)


@register_tool_filter("safety_filter")
class SafetyFilter(BaseFilter):
    """Filter that scans tool scripts for security risks before execution.

    This filter intercepts tool execution, scans the script content
    in the tool arguments using the SafetyScanner, and blocks execution
    if dangerous patterns are detected.

    The filter is auto-registered with the name ``safety_filter``.
    Enable it on a tool by passing ``filters_name=["safety_filter"]``::

        tool = BashTool(filters_name=["safety_filter"])
    """

    def __init__(
        self,
        policy_path: Optional[str] = None,
        policy: Optional[SafetyPolicy] = None,
    ) -> None:
        """Initialize the SafetyFilter.

        Args:
            policy_path: Path to the ``tool_safety_policy.yaml`` file.
                If not provided, defaults to ``tool_safety_policy.yaml``
                in the project root.
            policy: An already-loaded SafetyPolicy instance. If provided,
                ``policy_path`` is ignored.
        """
        super().__init__()
        if policy is not None:
            self._policy = policy
        else:
            path = policy_path or _DEFAULT_POLICY_PATH
            if os.path.exists(path):
                self._policy = SafetyPolicy.from_file(path)
                logger.info("SafetyFilter: Loaded policy from %s", path)
            else:
                logger.warning(
                    "SafetyFilter: Policy file not found at %s, "
                    "using default empty policy. Create tool_safety_policy.yaml "
                    "to enable full protection.",
                    path,
                )
                self._policy = SafetyPolicy()
        self._scanner = SafetyScanner(self._policy)
        self._audit_logger = AuditLogger()

    async def _before(
        self,
        ctx: AgentContext,
        req: Any,
        rsp: FilterResult,
    ) -> None:
        """Execute safety scan before tool execution.

        Extracts script content from the tool arguments, runs the
        safety scanner, and blocks execution if the script is dangerous.

        Args:
            ctx: The agent context.
            req: The tool arguments (dict).
            rsp: The filter result — set ``is_continue=False`` to block.
        """
        if not isinstance(req, dict):
            return

        # Extract script content from tool arguments
        script_content = self._extract_script_content(req)
        if not script_content:
            return

        # Determine script type
        script_type = self._detect_script_type(req, script_content)

        # Get tool name from context
        tool_name = self._get_tool_name(ctx, req)

        # Build scan input
        scan_input = ScanInput(
            script_content=script_content,
            script_type=script_type,
            command_line_args=req.get("args") or req.get("cmd_args"),
            working_directory=req.get("cwd") or req.get("working_directory"),
            env_vars=req.get("env") or req.get("env_vars"),
            tool_name=tool_name,
            tool_metadata={"tool_type": self._get_tool_type(ctx)},
        )

        # Run the scan
        report = self._scanner.scan(scan_input)

        # Log audit event
        self._audit_logger.log_report(report)

        # Block if denied
        if report.is_blocked:
            rsp.is_continue = False
            rsp.error = SafetyBlockedError(
                tool_name=tool_name,
                report=report,
            )
            logger.warning(
                "SafetyFilter: Blocked execution of tool '%s' — "
                "decision=%s risk=%s rules=%s",
                tool_name,
                report.decision.name,
                report.risk_level.name,
                ",".join(m.rule_id for m in report.matches),
            )
        elif report.needs_review:
            # For NEEDS_HUMAN_REVIEW, we log but allow (configurable)
            logger.info(
                "SafetyFilter: Tool '%s' needs human review — "
                "decision=%s risk=%s rules=%s",
                tool_name,
                report.decision.name,
                report.risk_level.name,
                ",".join(m.rule_id for m in report.matches),
            )

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_script_content(args: dict) -> Optional[str]:
        """Extract script content from tool arguments.

        Checks common argument names used by script-executing tools.

        Args:
            args: The tool arguments dictionary.

        Returns:
            The script content string, or None if not found.
        """
        for key in ("command", "content", "script", "code", "cmd", "body"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _detect_script_type(args: dict, content: str) -> ScriptType:
        """Detect the script type from tool arguments.

        Args:
            args: The tool arguments dictionary.
            content: The script content.

        Returns:
            The detected ScriptType.
        """
        # If the tool has a "language" or "type" field, use it
        lang = args.get("language") or args.get("type") or ""
        if "python" in lang.lower():
            return ScriptType.PYTHON
        if "bash" in lang.lower() or "sh" in lang.lower() or "shell" in lang.lower():
            return ScriptType.BASH

        # Heuristic detection via SafetyScanner
        from ._scanner import SafetyScanner  # noqa: E811
        return SafetyScanner._detect_script_type(content)

    @staticmethod
    def _get_tool_name(ctx: AgentContext, args: dict) -> str:
        """Get the tool name from context or arguments."""
        name = args.get("name") or args.get("tool_name") or ""
        if name:
            return name
        # Try to get from context tool var
        try:
            from trpc_agent_sdk.tools._context_var import get_tool_var  # noqa: E811
            tool = get_tool_var()
            if tool:
                return tool.name
        except Exception:  # pylint: disable=broad-except
            pass
        return "unknown"

    @staticmethod
    def _get_tool_type(ctx: AgentContext) -> str:
        """Get the tool type from context."""
        try:
            from trpc_agent_sdk.tools._context_var import get_tool_var  # noqa: E811
            tool = get_tool_var()
            if tool:
                return type(tool).__name__
        except Exception:  # pylint: disable=broad-except
            pass
        return "unknown"


class SafetyBlockedError(Exception):
    """Exception raised when a tool execution is blocked by the safety filter.

    Attributes:
        tool_name: Name of the blocked tool.
        report: The SafetyReport with scan results.
    """

    def __init__(
        self,
        tool_name: str,
        report: SafetyReport,
    ) -> None:
        self.tool_name = tool_name
        self.report = report
        rule_ids = ",".join(m.rule_id for m in report.matches)
        super().__init__(f"Tool '{tool_name}' execution blocked by safety filter: "
                         f"decision={report.decision.name}, "
                         f"risk={report.risk_level.name}, "
                         f"rules=[{rule_ids}]")
