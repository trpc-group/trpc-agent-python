# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Filter integration for the Tool Script Safety Guard.

:class:`ToolSafetyFilter` is a :class:`~trpc_agent_sdk.filter.BaseFilter`
that plugs into the Tool execution pipeline.  It runs *before* the tool's
``_run_async_impl`` and scans any script/command found in the tool
arguments.  If the scan returns ``deny``, the filter short-circuits the
pipeline and returns an error dict instead of executing the tool.

Usage — attach to a single tool::

    from trpc_agent_sdk.tools.safety import ToolSafetyFilter, SafetyGuard

    guard = SafetyGuard.default()
    bash_tool = BashTool(filters=[ToolSafetyFilter(guard)])

Usage — register globally so *all* tools get scanned::

    from trpc_agent_sdk.filter import register_tool_filter
    from trpc_agent_sdk.tools.safety import ToolSafetyFilter

    @register_tool_filter("safety_guard")
    class GlobalSafetyFilter(ToolSafetyFilter):
        def __init__(self):
            super().__init__(SafetyGuard.default())

    # Then in tool construction:
    BashTool(filters_name=["safety_guard"])
"""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.tools import get_tool_var

from ._audit import AuditLogger
from ._models import Decision
from ._safety_guard import SafetyGuard

# Fields in tool args that typically contain a script or command.
# NOTE: Custom tools with non-standard field names (e.g. "sql", "query")
# are NOT scanned unless the field name is added to this tuple.  This is
# a known limitation — extend this tuple (or submit a patch) when adding
# support for new tool types.
_SCRIPT_FIELDS = ("command", "script", "code", "cmd", "shell_command", "expr")


def _extract_script(args: dict[str, Any]) -> tuple[str, str]:
    """Extract (script_content, field_name) from tool args.

    Returns ``("", "")`` if no script-like field is found.

    .. note::
       Only the fields listed in :data:`_SCRIPT_FIELDS` are inspected.
       Custom tool argument names (e.g. ``sql``, ``query``) are not
       scanned automatically — add them to ``_SCRIPT_FIELDS`` or use
       a naming convention that includes one of the recognised keys.
    """
    for field in _SCRIPT_FIELDS:
        val = args.get(field)
        if isinstance(val, str) and val.strip():
            return val, field
    # Some tools put the script under a nested "input" key.
    nested = args.get("input")
    if isinstance(nested, dict):
        for field in _SCRIPT_FIELDS:
            val = nested.get(field)
            if isinstance(val, str) and val.strip():
                return val, field
    elif isinstance(nested, str) and nested.strip():
        return nested, "input"
    return "", ""


class ToolSafetyFilter(BaseFilter):
    """Tool filter that scans scripts before execution.

    Args:
        guard: The :class:`SafetyGuard` used for scanning.
        block_on_review: If ``True``, ``needs_human_review`` also blocks
            execution (default ``False`` — only ``deny`` blocks).
        audit_logger: Optional explicit audit logger.  If ``None``, uses
            the guard's audit logger.
    """

    def __init__(
        self,
        guard: SafetyGuard,
        *,
        block_on_review: bool = False,
        audit_logger: Optional[AuditLogger] = None,
    ) -> None:
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_safety_guard"
        self.guard = guard
        self.block_on_review = block_on_review
        self.audit_logger = audit_logger or guard.audit_logger

    @override
    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Scan the tool args before execution.

        If the scan denies the script, sets ``rsp.rsp`` to an error dict
        and ``rsp.is_continue = False`` to short-circuit the pipeline.
        """
        if not isinstance(req, dict):
            return

        script, field_name = _extract_script(req)
        if not script:
            # No script to scan — let the tool run normally.
            return

        tool = get_tool_var()
        tool_name = tool.name if tool else "unknown"

        report = self.guard.scan(
            script=script,
            tool_name=tool_name,
            args=req,
            cwd=req.get("cwd", ""),
            env=req.get("env", {}),
        )

        should_block = report.decision == Decision.DENY
        if self.block_on_review and report.decision == Decision.NEEDS_HUMAN_REVIEW:
            should_block = True

        if should_block:
            # Build a structured error response that the agent can see.
            findings_summary = "; ".join(f"[{f.rule_id}] {f.description}" for f in report.findings)
            rsp.rsp = {
                "success": False,
                "error": "SAFETY_GUARD_BLOCKED",
                "decision": report.decision.value,
                "risk_level": report.risk_level.value,
                "blocked_by": "ToolSafetyFilter",
                "findings": [f.to_dict() for f in report.findings],
                "summary": findings_summary,
                "script_hash": report.script_hash,
            }
            rsp.is_continue = False
            rsp.error = None
            return

    @override
    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """After execution — nothing to do (audit is logged during scan)."""
        return None

    @override
    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """After every stream — nothing to do."""
        return None
