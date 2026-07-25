# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Tool Filter integration for pre-execution script safety checks."""

from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Optional

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterHandleType
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._audit import SafetyAuditSink
from ._audit import ensure_audit_sink
from ._models import Decision
from ._models import SafetyScanRequest
from ._scanner import ToolSafetyScanner
from ._telemetry import trace_safety_report

RequestExtractor = Callable[[dict[str, Any], str], Optional[SafetyScanRequest]]


class ToolScriptSafetyFilter(BaseFilter):
    """Block unsafe script-capable Tool, MCP Tool, or Skill calls."""

    def __init__(
        self,
        scanner: Optional[ToolSafetyScanner] = None,
        audit_sink: Optional[SafetyAuditSink] = None,
        request_extractor: Optional[RequestExtractor] = None,
        allow_human_review: bool = False,
    ):
        super().__init__()
        self.scanner = scanner or ToolSafetyScanner()
        self.audit_sink = ensure_audit_sink(audit_sink)
        self.request_extractor = request_extractor or default_request_extractor
        self.allow_human_review = allow_human_review

    async def run(self, ctx: AgentContext, req: Any, handle: FilterHandleType) -> FilterResult:
        """Scan before invoking the rest of the Tool filter chain."""
        if not isinstance(req, dict):
            return await handle()
        tool = get_tool_var()
        tool_name = getattr(tool, "name", "unknown")
        scan_request = self.request_extractor(req, tool_name)
        if scan_request is None:
            return await handle()

        report = self.scanner.scan(scan_request)
        blocked = (report.decision == Decision.DENY
                   or report.decision == Decision.NEEDS_HUMAN_REVIEW and not self.allow_human_review)
        self.audit_sink.emit(report.to_audit_event(blocked=blocked))
        trace_safety_report(report, blocked=blocked)
        if blocked:
            error = ("TOOL_SAFETY_BLOCKED" if report.decision == Decision.DENY else "TOOL_SAFETY_REVIEW_REQUIRED")
            return FilterResult(
                rsp={
                    "error": error,
                    "message": _blocked_message(report.decision),
                    "safety_report": report.model_dump(mode="json"),
                },
                is_continue=False,
            )
        return await handle()


def default_request_extractor(args: dict[str, Any], tool_name: str) -> Optional[SafetyScanRequest]:
    """Extract conventional script execution fields from Tool arguments."""
    script = next(
        (args[key] for key in ("script", "code", "command") if isinstance(args.get(key), str)),
        None,
    )
    if script is None:
        return None
    language = args.get("language")
    if not isinstance(language, str):
        language = "bash" if "command" in args else "python"
    command_args = args.get("args", args.get("command_args", []))
    if not isinstance(command_args, list):
        command_args = [] if command_args is None else [str(command_args)]
    else:
        command_args = [str(item) for item in command_args]
    environment = args.get("env", args.get("environment", {}))
    if not isinstance(environment, dict):
        environment = {}
    environment = {str(key): str(value) for key, value in environment.items() if isinstance(key, str)}
    timeout = args.get("timeout", args.get("timeout_seconds"))
    if not isinstance(timeout, (int, float)):
        timeout = None
    working_directory = args.get("cwd", args.get("working_directory"))
    if not isinstance(working_directory, str):
        working_directory = None
    return SafetyScanRequest(
        script=script,
        language=language,
        command_args=command_args,
        working_directory=working_directory,
        environment=environment,
        tool_name=tool_name,
        timeout_seconds=timeout,
    )


def _blocked_message(decision: Decision) -> str:
    if decision == Decision.DENY:
        return "The script was blocked by the tool safety policy."
    return "The script requires explicit human security review before execution."
