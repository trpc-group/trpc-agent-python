# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool filter that applies safety checks before tool execution."""

from __future__ import annotations

from typing import Any
from typing import Optional

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.tools._context_var import get_tool_var

from .audit import SafetyAuditLogger
from .audit import monotonic_ms
from .bash_scanner import create_bash_rules
from .checker import SafetyChecker
from .models import SafetyDecision
from .models import SafetyResult
from .models import ToolExecutionRequest
from .policy import SafetyPolicy
from .python_scanner import create_python_rules
from .report import SafetyReportWriter
from .telemetry import record_safety_attributes

_SCRIPT_ARG_KEYS = ("script", "code", "command", "cmd", "python_code", "bash_code")
_LANGUAGE_ARG_KEYS = ("language", "lang")
_PYTHON_TOOL_HINTS = ("python",)
_BASH_TOOL_HINTS = ("bash", "shell", "sh")


class ToolSafetyFilter(BaseFilter):
    """Run the tool safety checker before executing a tool."""

    def __init__(
        self,
        checker: Optional[SafetyChecker] = None,
        policy: Optional[SafetyPolicy] = None,
        audit_logger: Optional[SafetyAuditLogger] = None,
        report_writer: Optional[SafetyReportWriter] = None,
    ):
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_safety"
        self._checker = checker or SafetyChecker(rules=create_python_rules() + create_bash_rules(), policy=policy)
        self._policy = policy
        self._audit_logger = audit_logger or SafetyAuditLogger()
        self._report_writer = report_writer or SafetyReportWriter()

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Run safety checks before the actual tool implementation."""
        request = _build_tool_execution_request(req)
        start_ms = monotonic_ms()
        result = await self._checker.check(request, self._policy)
        self._audit_logger.write(result, monotonic_ms(start_ms))
        self._report_writer.write(result)
        record_safety_attributes(result)

        if result.decision == SafetyDecision.ALLOW:
            return

        rsp.rsp = _safety_response(result)
        rsp.is_continue = False
        rsp.error = None


def _build_tool_execution_request(args: Any) -> ToolExecutionRequest:
    invocation_ctx = get_invocation_ctx()
    tool = get_tool_var()
    tool_name = getattr(tool, "name", "")
    safe_args = args if isinstance(args, dict) else {}
    language = _extract_language(tool_name, safe_args)
    script = _extract_script(safe_args)
    return ToolExecutionRequest(
        tool_name=tool_name,
        args=safe_args,
        language=language,
        script=script,
        agent_name=getattr(invocation_ctx, "agent_name", ""),
        invocation_id=getattr(invocation_ctx, "invocation_id", ""),
        function_call_id=getattr(invocation_ctx, "function_call_id", "") or "",
        metadata={
            "filter": "tool_safety",
        },
    )


def _extract_script(args: dict[str, Any]) -> str:
    for key in _SCRIPT_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_language(tool_name: str, args: dict[str, Any]) -> str:
    for key in _LANGUAGE_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

    lowered_tool_name = tool_name.lower()
    if any(hint in lowered_tool_name for hint in _PYTHON_TOOL_HINTS):
        return "python"
    if any(hint in lowered_tool_name for hint in _BASH_TOOL_HINTS):
        return "bash"
    if isinstance(args.get("python_code"), str):
        return "python"
    if isinstance(args.get("bash_code"), str):
        return "bash"
    return ""


def _safety_response(result: SafetyResult) -> dict[str, Any]:
    response = {
        "status": "blocked" if result.decision == SafetyDecision.DENY else "needs_human_review",
        "decision": result.decision.value,
        "message": _decision_message(result),
        "findings": [_finding_dict(finding) for finding in result.findings],
    }
    if result.decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
        response["human_review_required"] = True
    return response


def _decision_message(result: SafetyResult) -> str:
    if result.decision == SafetyDecision.DENY:
        return "Tool execution was denied by the safety policy."
    return "Tool execution requires human review before it can continue."


def _finding_dict(finding) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "message": finding.message,
        "severity": finding.severity.value,
        "target": finding.target,
        "metadata": finding.metadata,
    }
