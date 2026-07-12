# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Execution guard, audit sink, and Tool Filter integration."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable

from opentelemetry import trace

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.log import logger

from ._models import SafetyDecision
from ._models import ToolSafetyReport
from ._models import ToolSafetyRequest
from ._scanner import ToolScriptSafetyScanner


class ToolSafetyBlockedError(PermissionError):
    """Raised by the wrapper when policy blocks execution."""

    def __init__(self, report: ToolSafetyReport):
        super().__init__(f"Tool execution blocked: {report.decision.value} ({', '.join(report.rule_ids)})")
        self.report = report


class ToolSafetyResourceLimitError(RuntimeError):
    """Raised when an allowed execution exceeds a configured runtime limit."""


class JsonlAuditSink:
    """Append redacted safety decisions as one JSON object per line."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def emit(self, report: ToolSafetyReport) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool_name": report.tool_name,
            "decision": report.decision.value,
            "risk_level": report.risk_level.value,
            "rule_id": report.rule_ids,
            "duration_ms": report.duration_ms,
            "redacted": report.redacted,
            "blocked": report.blocked,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def set_safety_span_attributes(report: ToolSafetyReport) -> None:
    """Attach the stable safety schema when an OpenTelemetry span is active."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    for key, value in report.telemetry_attributes.items():
        span.set_attribute(key, value)
    span.set_attribute("tool.safety.blocked", report.blocked)
    span.set_attribute("tool.safety.redacted", report.redacted)
    span.set_attribute("tool.safety.duration_ms", report.duration_ms)


class ToolSafetyGuard:
    """Scan, audit and gate a callable before it executes."""

    def __init__(
        self,
        scanner: ToolScriptSafetyScanner | None = None,
        audit_sink: JsonlAuditSink | None = None,
    ):
        self.scanner = scanner or ToolScriptSafetyScanner()
        self.audit_sink = audit_sink

    def check(self, request: ToolSafetyRequest) -> ToolSafetyReport:
        report = self.scanner.scan(request)
        audit_event = {
            "event": "tool_safety_decision",
            "tool_name": report.tool_name,
            "decision": report.decision.value,
            "risk_level": report.risk_level.value,
            "rule_id": report.rule_ids,
            "duration_ms": report.duration_ms,
            "redacted": report.redacted,
            "blocked": report.blocked,
        }
        logger.info("tool safety audit: %s", json.dumps(audit_event, ensure_ascii=False))
        if self.audit_sink:
            self.audit_sink.emit(report)
        set_safety_span_attributes(report)
        return report

    async def run(
        self,
        request: ToolSafetyRequest,
        executor: Callable[[], Any | Awaitable[Any]],
    ) -> Any:
        report = self.check(request)
        approved = bool(request.metadata.get("human_approved"))
        if report.decision == SafetyDecision.DENY or (
            report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW and not approved
        ):
            raise ToolSafetyBlockedError(report)
        result = executor()
        if inspect.isawaitable(result):
            requested_timeout = request.metadata.get("timeout")
            timeout = self.scanner.policy.max_timeout_seconds
            if isinstance(requested_timeout, (int, float)) and requested_timeout > 0:
                timeout = min(float(requested_timeout), timeout)
            try:
                result = await asyncio.wait_for(result, timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise ToolSafetyResourceLimitError(
                    f"Tool execution exceeded {timeout} seconds"
                ) from exc
        if isinstance(result, bytes):
            output_size = len(result)
        elif isinstance(result, str):
            output_size = len(result.encode("utf-8"))
        else:
            output_size = len(json.dumps(result, default=str).encode("utf-8"))
        if output_size > self.scanner.policy.max_output_bytes:
            raise ToolSafetyResourceLimitError(
                f"Tool output exceeded {self.scanner.policy.max_output_bytes} bytes"
            )
        return result


@register_tool_filter("tool_script_safety")
class ToolScriptSafetyFilter(BaseFilter):
    """Tool Filter that blocks unsafe ``script``/``code``/``command`` args."""

    def __init__(self):
        super().__init__()
        self.guard = ToolSafetyGuard()

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        if not isinstance(req, dict):
            return
        script = req.get("script") or req.get("code") or req.get("command")
        if not isinstance(script, str):
            return
        try:
            from trpc_agent_sdk.tools import get_tool_var

            tool = get_tool_var()
            tool_name = getattr(tool, "name", "unknown_tool")
        except (LookupError, RuntimeError):
            tool_name = "unknown_tool"
        request = ToolSafetyRequest(
            tool_name=tool_name,
            script=script,
            language=req.get("language", "auto"),
            command_args=[str(value) for value in req.get("command_args", [])],
            working_directory=req.get("cwd") or req.get("working_directory"),
            environment={str(key): str(value) for key, value in req.get("env", {}).items()},
            metadata={"timeout": req.get("timeout"), "human_approved": req.get("human_approved", False)},
        )
        report = self.guard.check(request)
        approved = bool(request.metadata.get("human_approved"))
        if report.decision == SafetyDecision.DENY or (
            report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW and not approved
        ):
            rsp.rsp = {"success": False, "safety_report": report.model_dump(mode="json")}
            rsp.is_continue = False
