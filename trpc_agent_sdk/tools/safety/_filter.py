#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tool Filter integration for pre-execution script checks."""

from __future__ import annotations

from typing import Any
from typing import Callable

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._adapter import default_request_extractor
from ._guard import ToolSafetyGuard
from ._models import BlockedSafetyResponse
from ._models import SafetyDecision
from ._models import ScriptScanRequest

RequestExtractor = Callable[..., ScriptScanRequest | None]


@register_tool_filter("tool_script_safety")
class ToolScriptSafetyFilter(BaseFilter):
    """Block deny and unapproved review decisions before a Tool handler."""

    def __init__(
        self,
        guard: ToolSafetyGuard | None = None,
        request_extractor: RequestExtractor | None = None,
    ):
        super().__init__()
        self.guard = guard or ToolSafetyGuard()
        self.request_extractor = request_extractor or default_request_extractor

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        del ctx
        tool = get_tool_var()
        tool_name = getattr(tool, "name", "unknown_tool") if tool is not None else "unknown_tool"
        try:
            request = self.request_extractor(req, tool_name=tool_name)
        except Exception as exc:  # pylint: disable=broad-except
            report = self.guard.scanner.failure_report(exc, rule_id="SCAN_INPUT_ERROR")
            report = self.guard.record(tool_name, report)
            rsp.rsp = _blocked_response(report)
            rsp.error = None
            rsp.is_continue = False
            return
        if request is None:
            return

        report = self.guard.check(request)
        if report.blocked:
            rsp.rsp = _blocked_response(report)
            rsp.error = None
            rsp.is_continue = False


def _blocked_response(report) -> dict[str, Any]:
    review_required = report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    error = "TOOL_SAFETY_REVIEW_REQUIRED" if review_required else "TOOL_SAFETY_BLOCKED"
    message = (
        "Tool execution requires trusted human approval."
        if review_required
        else "Tool execution was denied by the safety policy."
    )
    return BlockedSafetyResponse(
        error=error,
        message=message,
        review_required=review_required,
        safety_report=report.model_dump(mode="json"),
    ).model_dump(mode="json")
