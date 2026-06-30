# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pre-execution Tool Filter for the Tool Script Safety Guard.

``ToolSafetyFilter`` is a ``BaseFilter`` registered as ``tool_safety_guard``.
Attach it with ``filters_name=["tool_safety_guard"]`` on any tool.

It overrides ``run()`` and -- crucially -- decides **before** calling
``handle()`` (the call that actually runs the tool). On a ``DENY`` decision it
returns a blocked :class:`FilterResult` with ``is_continue=False`` and never
invokes ``handle()``, so the tool body does not execute. Every scan, blocked or
not, writes one auditable event and (when tracing is active) an OTel span.

The policy is loaded once at construction from ``TOOL_SAFETY_POLICY_PATH`` (or
the built-in default); the audit path comes from ``TOOL_SAFETY_AUDIT_PATH``.
"""

from __future__ import annotations

import os
from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterHandleType
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools._context_var import get_tool_var

from .audit import ENV_AUDIT_PATH
from .audit import AuditLogger
from .audit import emit_safety_span
from .engine import SafetyEngine
from .models import Decision
from .models import Language
from .models import SafetyReport
from .models import ScanInput
from .policy import SafetyPolicy
from .policy import load_policy

_LANG_MAP = {
    "python": Language.PYTHON,
    "py": Language.PYTHON,
    "bash": Language.BASH,
    "sh": Language.BASH,
    "shell": Language.BASH,
}


def to_language(name: str) -> Language:
    """Map a policy language token to a :class:`Language`."""
    return _LANG_MAP.get((name or "").lower(), Language.UNKNOWN)


def extract_scan_input(tool_name: str, args: dict[str, Any], policy: SafetyPolicy) -> ScanInput:
    """Locate the payload to scan inside a tool's ``args`` (design doc 3.5).

    Uses ``policy.param_keys`` (tool-name keyword -> arg keys + language). When
    nothing matches, falls back to scanning every string argument joined
    together (fail-safe: never silently skip a payload).
    """
    tn = (tool_name or "unknown").lower()
    for keyword, group in policy.param_keys.items():
        if keyword.lower() in tn:
            for key in group.keys:
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    return ScanInput(
                        script=value,
                        tool_name=tool_name,
                        language=to_language(group.language),
                        args=args,
                        cwd=args.get("cwd"),
                    )
    # Fallback: scan all string values (conservative).
    parts = [v for v in args.values() if isinstance(v, str) and v.strip()]
    return ScanInput(
        script="\n".join(parts),
        tool_name=tool_name,
        language=Language.UNKNOWN,
        args=args,
        cwd=args.get("cwd"),
    )


def build_blocked_result(report: SafetyReport) -> dict[str, Any]:
    """The tool result returned when execution is blocked."""
    primary = report.findings[0].rule_id if report.findings else None
    return {
        "success": False,
        "blocked": True,
        "error": (f"TOOL_SAFETY_DENIED: execution blocked by the safety guard "
                  f"(rule={primary}, risk={report.risk_level.value})"),
        "safety": report.to_dict(),
    }


@register_tool_filter("tool_safety_guard")
class ToolSafetyFilter(BaseFilter):
    """Pre-execution safety gate. Blocks tools whose decision is ``DENY``."""

    #: Decisions that block execution. ``needs_human_review`` does NOT block --
    #: it is informational and routed to a human out of band (design doc 5).
    BLOCK_DECISIONS = frozenset({Decision.DENY})

    def __init__(self) -> None:
        super().__init__()
        self._engine = SafetyEngine(load_policy())
        self._audit = AuditLogger(os.environ.get(ENV_AUDIT_PATH))

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        tool = None
        try:
            tool = get_tool_var()
        except Exception:  # pylint: disable=broad-except
            tool = None
        tool_name = getattr(tool, "name", None) or "unknown"
        args = req if isinstance(req, dict) else {}

        report = self._engine.scan(extract_scan_input(tool_name, args, self._engine.policy))
        blocked = report.decision in self.BLOCK_DECISIONS

        # Auditable event + observability for every scan.
        self._audit.log(report, blocked)
        emit_safety_span(report, blocked)

        if blocked:
            # Do NOT call handle(): the tool body never runs.
            return FilterResult(rsp=build_blocked_result(report), is_continue=False)

        return await handle()
