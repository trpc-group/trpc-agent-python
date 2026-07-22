# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Filter example for pre-execution script safety checks."""

from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import get_tool_var

from ._audit import write_audit_event
from ._scanner import ToolScriptSafetyScanner
from ._telemetry import record_safety_attributes
from ._types import Decision
from ._types import SafetyReport
from ._types import ToolScriptScanRequest

_SCRIPT_ARG_KEYS = ("script", "code", "command", "cmd", "python_code", "bash_code")
_LANGUAGE_ARG_KEYS = ("language", "lang")
_COMMAND_ARGS_KEYS = ("command_args", "args", "argv")


class ToolSafetyFilter(BaseFilter):
    """Tool filter that blocks script execution requests before the handler runs.

    The request is expected to be a mapping with script-like fields such as
    ``script``, ``code``, ``command``, ``cmd``, ``python_code``, ``bash_code``,
    or ``code_blocks``. This keeps the filter reusable for Tool, Skill, MCP,
    and CodeExecutor wrappers.
    """

    def __init__(
        self,
        scanner: ToolScriptSafetyScanner | None = None,
        audit_log_path: str | Path | None = None,
        block_on_review: bool = False,
    ):
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_script_safety"
        self.scanner = scanner or ToolScriptSafetyScanner()
        self.audit_log_path = audit_log_path
        self.block_on_review = block_on_review
        self._current_report: ContextVar[SafetyReport | None] = ContextVar(
            f"{self._name}_{id(self)}_report",
            default=None,
        )

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        self._current_report.set(None)
        if not isinstance(req, dict):
            return None
        script = _extract_script(req)
        if not script:
            return None
        tool_name = _extract_tool_name(req)
        request = ToolScriptScanRequest(
            script=script,
            language=_extract_language(req, tool_name),
            command_args=_extract_command_args(req),
            cwd=str(req.get("cwd", "")),
            env=dict(req.get("env", {}) or {}),
            tool_name=tool_name,
            tool_metadata=dict(req.get("tool_metadata", {}) or {}),
        )
        report = self.scanner.scan(request)
        should_block = report.decision == Decision.DENY or (self.block_on_review
                                                            and report.decision == Decision.NEEDS_HUMAN_REVIEW)
        report.set_blocked(should_block)
        record_safety_attributes(report)
        if self.audit_log_path:
            write_audit_event(self.audit_log_path, report)
        if report.blocked:
            rsp.rsp = report.to_dict()
            rsp.error = PermissionError(report.summary)
            rsp.is_continue = False
        else:
            self._current_report.set(report)
            rsp.rsp = report.to_dict()
        return None

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        report = self._current_report.get()
        self._current_report.set(None)
        if report is None or rsp.error:
            return None
        rsp.rsp = _attach_safety_report(rsp.rsp, report)
        return None


def _extract_script(req: dict[str, Any]) -> str:
    for key in _SCRIPT_ARG_KEYS:
        value = req.get(key)
        if isinstance(value, str) and value.strip():
            return value

    code_blocks = req.get("code_blocks")
    if isinstance(code_blocks, list):
        parts: list[str] = []
        for block in code_blocks:
            if isinstance(block, dict):
                code = block.get("code", "")
            else:
                code = getattr(block, "code", "")
            if isinstance(code, str) and code:
                parts.append(code)
        if parts:
            return "\n".join(parts)
    return ""


def _extract_tool_name(req: dict[str, Any]) -> str:
    explicit_name = req.get("tool_name")
    if isinstance(explicit_name, str) and explicit_name.strip():
        return explicit_name
    current_tool = get_tool_var()
    current_name = getattr(current_tool, "name", "")
    if isinstance(current_name, str) and current_name.strip():
        return current_name
    return "unknown_tool"


def _extract_language(req: dict[str, Any], tool_name: str) -> str:
    for key in _LANGUAGE_ARG_KEYS:
        value = req.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    if isinstance(req.get("python_code"), str) or "code" in req:
        return "python"
    if isinstance(req.get("bash_code"), str) or "command" in req or "cmd" in req:
        return "bash"
    lowered_tool_name = tool_name.lower()
    if "python" in lowered_tool_name:
        return "python"
    if any(hint in lowered_tool_name for hint in ("bash", "shell", "sh")):
        return "bash"
    return "unknown"


def _extract_command_args(req: dict[str, Any]) -> list[str]:
    for key in _COMMAND_ARGS_KEYS:
        value = req.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
    return []


def _attach_safety_report(response: Any, report: SafetyReport) -> Any:
    report_dict = report.to_dict()
    if isinstance(response, dict):
        if "safety_report" not in response:
            response = dict(response)
            response["safety_report"] = report_dict
        return response

    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            return response
        if isinstance(parsed, dict) and "safety_report" not in parsed:
            parsed["safety_report"] = report_dict
            return json.dumps(parsed, ensure_ascii=False)

    return response
