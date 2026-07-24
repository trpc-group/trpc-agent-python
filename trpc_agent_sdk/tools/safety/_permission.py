# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Permission policy integration for the tool safety scanner.

Wraps the scanner as a tool-level Filter so that every tool
invocation is checked before execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trpc_agent_sdk.abc import FilterABC, FilterResult, FilterType
from trpc_agent_sdk.context import AgentContext

from ._types import Request, CodeBlock, DECISION_DENY, DECISION_ALLOW
from ._scanner import scan
from ._policy import default_policy, Policy

logger = logging.getLogger(__name__)

SAFETY_FILTER_NAME = "tool_safety_guard"


class ToolSafetyFilter(FilterABC):
    """Pre-execution safety filter for tool calls.

    Scans commands and code blocks before they are executed by
    workspace_exec, exec_command, or execute_code tools.
    """

    def __init__(self, policy: Policy | None = None) -> None:
        self._policy = policy or default_policy()
        self._name = SAFETY_FILTER_NAME
        self._type = FilterType.TOOL

    @property
    def name(self) -> str:
        return self._name

    @property
    def type(self) -> FilterType:
        return self._type

    async def _before(
        self,
        ctx: AgentContext,
        req: Any,
        rsp: FilterResult,
    ) -> None:
        """Scan tool arguments before execution."""
        if req is None:
            return

        scan_req = _to_scan_request(req)
        if scan_req is None:
            return  # Not a scannable tool.

        report = scan(scan_req, self._policy)

        if report.decision == DECISION_DENY:
            rsp.error = PermissionError(f"Tool safety guard blocked: {report.recommendation}")
            rsp.is_continue = False
            logger.warning(
                "safety_guard blocked tool=%s decision=%s rule=%s",
                scan_req.tool_name,
                report.decision.value,
                report.rule_id,
            )
        elif report.decision != DECISION_ALLOW:
            logger.info(
                "safety_guard %s tool=%s rule=%s",
                report.decision.value,
                scan_req.tool_name,
                report.rule_id,
            )

    async def _after(
        self,
        ctx: AgentContext,
        req: Any,
        rsp: FilterResult,
    ) -> None:
        pass  # No post-execution check needed.


def _to_scan_request(tool_req: Any) -> Request | None:
    """Convert a tool invocation to a safety scan Request."""
    if not hasattr(tool_req, "tool_name"):
        return None

    tool_name = getattr(tool_req, "tool_name", "")
    if tool_name not in ("workspace_exec", "exec_command", "execute_code"):
        return None

    args_raw = getattr(tool_req, "arguments", None)
    if args_raw is None:
        return Request(tool_name=tool_name)

    if isinstance(args_raw, bytes):
        args_raw = args_raw.decode("utf-8", errors="replace")
    if isinstance(args_raw, str):
        try:
            args_raw = json.loads(args_raw)
        except json.JSONDecodeError:
            pass
    if not isinstance(args_raw, dict):
        return Request(tool_name=tool_name)

    command = str(
        args_raw.get("command", "") or args_raw.get("cmd", "") or args_raw.get("script", "")
        or args_raw.get("code", "") or "")
    code_blocks_raw = args_raw.get("code_blocks")
    code_blocks: list[CodeBlock] = []
    if code_blocks_raw:
        if isinstance(code_blocks_raw, list):
            for cb in code_blocks_raw:
                if isinstance(cb, dict):
                    code_blocks.append(CodeBlock(
                        language=str(cb.get("language", "")),
                        code=str(cb.get("code", "")),
                    ))

    return Request(
        tool_name=tool_name,
        command=command,
        cwd=str(args_raw.get("cwd", "") or args_raw.get("workdir", "")),
        env=args_raw.get("env") if isinstance(args_raw.get("env"), dict) else {},
        backend="hostexec"
        if tool_name == "exec_command" else "codeexec" if tool_name == "execute_code" else "workspaceexec",
        timeout_seconds=int(args_raw.get("timeout_sec", 0) or args_raw.get("timeout", 0) or 0),
        background=bool(args_raw.get("background", False)),
        tty=bool(args_raw.get("tty", False) or args_raw.get("pty", False)),
        code_blocks=code_blocks,
    )
