# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool safety filter for the TRPC Agent filter pipeline.

Integrates the ToolSafetyScanner as a BaseFilter that runs in _before()
to inspect tool arguments and block dangerous scripts before execution.
"""

from __future__ import annotations

from typing import Any
from typing import Optional

from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.context import AgentContext

from ._audit import SafetyAuditLogger
from ._scanner import ToolSafetyScanner
from ._telemetry import set_safety_span_attrs
from ._types import Decision


class ToolSafetyFilter(BaseFilter):
    """A BaseFilter that scans tool script arguments before execution.

    Plugs into the existing tool filter chain via FilterRunner._run_filters().
    Blocks execution if the safety scanner returns DENY. Writes audit events
    and sets OpenTelemetry span attributes on every scan.
    """

    def __init__(
        self,
        *,
        scanner: ToolSafetyScanner,
        audit_logger: Optional[SafetyAuditLogger] = None,
    ):
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_safety"
        self._scanner = scanner
        self._audit_logger = audit_logger or SafetyAuditLogger()

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        script = self._extract_script(req)
        if not script:
            return

        tool_name = getattr(req, "tool_name", "unknown")
        args = getattr(req, "args", None)
        env_vars = getattr(req, "env_vars", None)

        report = await self._scanner.scan(
            script=script,
            tool_name=tool_name,
            args=args,
            env_vars=env_vars,
        )

        set_safety_span_attrs(report)

        script_hash = self._scanner.hash_script(script)
        self._audit_logger.log(report, tool_name=tool_name, script_hash=script_hash)

        if report.decision == Decision.DENY:
            findings_text = "; ".join(
                f"{f.rule_id}: {f.message}" for f in report.findings
            )
            rsp.error = Exception(
                f"Tool execution blocked by safety guard: {findings_text}"
            )
            rsp.is_continue = False

    @staticmethod
    def _extract_script(req: Any) -> Optional[str]:
        args = getattr(req, "args", None)
        if not args or not isinstance(args, dict):
            return None

        for key in ("command", "script", "code", "text", "content"):
            if key in args and isinstance(args[key], str) and args[key].strip():
                return args[key]

        str_values = [v for v in args.values() if isinstance(v, str) and len(v) > 10]
        if str_values:
            return "\n".join(str_values)

        return None
