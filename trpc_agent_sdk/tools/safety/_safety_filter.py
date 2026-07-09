# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool safety filter: scans a script before a tool's _run_async_impl runs.

Registered as a tool filter named "tool_safety". Attach to any tool via
`filters_name=["tool_safety"]` or `add_one_filter("tool_safety")`.
"""
from __future__ import annotations

import os
from typing import Any
from typing import Optional
from typing import Tuple

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterHandleType
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import register_tool_filter

from trpc_agent_sdk.tools.safety._policy import Policy
from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._scanner import scan
from trpc_agent_sdk.tools.safety._types import Decision

# Fields in tool args that may carry an executable script/command.
_SCRIPT_FIELDS = ("code", "script", "command", "cmd", "file")


def extract_script(req: Any) -> Optional[Tuple[str, str]]:
    """Return (script, language_hint) if req looks like it carries a script."""
    if not isinstance(req, dict):
        return None
    for field in _SCRIPT_FIELDS:
        val = req.get(field)
        if isinstance(val, str) and val.strip():
            return val, "auto"
    return None


@register_tool_filter("tool_safety")
class ToolSafetyFilter(BaseFilter):
    """Block scripts whose scan decision is not ALLOW.

    Interception happens in run() (the non-streaming tool path that BaseTool uses).
    Streaming tool paths inherit BaseFilter.run_stream defaults and are not scanned
    (MVP limitation).
    """

    def __init__(self, policy: Optional[Policy] = None) -> None:
        super().__init__()
        from trpc_agent_sdk.abc import FilterType
        self._type = FilterType.TOOL
        self._name = "tool_safety"
        self._policy = policy

    def _ensure_policy(self) -> Policy:
        if self._policy is None:
            path = os.environ.get("TRPC_AGENT_TOOL_SAFETY_POLICY")
            self._policy = load_policy(path)
        return self._policy

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        extracted = extract_script(req)
        if extracted is None:
            # Not a script-bearing tool call; pass through.
            return await handle()

        script, language = extracted
        report = scan(self._ensure_policy(), script, language=language,
                      meta={"tool_name": getattr(ctx, "tool_name", None)})
        if report.decision == Decision.ALLOW:
            return await handle()

        # DENY or NEEDS_REVIEW: intercept, do not invoke handle().
        rule_ids = sorted({f.rule_id for f in report.findings})
        return FilterResult(
            rsp={
                "success": False,
                "error": "TOOL_SAFETY_BLOCKED",
                "decision": report.decision.name,
                "risk_level": report.risk_level.name,
                "rule_ids": rule_ids,
                "recommendation": report.recommendation,
            },
            is_continue=False,
        )


async def _run_filter_direct(flt: ToolSafetyFilter, req: Any, handle) -> Any:
    """Test helper: invoke the filter's run() exactly once with a real handle.

    Exposed for unit tests so they don't need the full FilterRunner chain.
    Production wiring uses the framework's run_filters() automatically.
    """
    from trpc_agent_sdk.context import AgentContext
    ctx = AgentContext()
    result = await flt.run(ctx, req, handle)
    return result.rsp if isinstance(result, FilterResult) else result
