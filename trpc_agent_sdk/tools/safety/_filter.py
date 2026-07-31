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

_PYTHON_ARG_KEYS = ("python_code", )
_BASH_ARG_KEYS = ("command", "cmd", "bash_code")
_GENERIC_ARG_KEYS = ("script", )
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
        tool_name = _extract_tool_name(req)
        requests = _extract_scan_requests(req, tool_name)
        if not requests:
            return None
        report = self.scanner.scan_segments(requests)
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


def _extract_scan_requests(req: dict[str, Any], tool_name: str) -> list[ToolScriptScanRequest]:
    grouped_parts: dict[str, list[str]] = {}

    for key in _PYTHON_ARG_KEYS:
        _add_script_part(grouped_parts, "python", req.get(key))
    for key in _BASH_ARG_KEYS:
        _add_script_part(grouped_parts, "bash", req.get(key))

    generic_language = _extract_language(req, tool_name)
    for key in _GENERIC_ARG_KEYS:
        _add_script_part(grouped_parts, generic_language, req.get(key))
    code_language = _extract_explicit_language(req) or "python"
    _add_script_part(grouped_parts, code_language, req.get("code"))

    code_blocks = req.get("code_blocks")
    if isinstance(code_blocks, list):
        for block in code_blocks:
            if isinstance(block, dict):
                code = block.get("code", "")
                language = block.get("language", "")
            else:
                code = getattr(block, "code", "")
                language = getattr(block, "language", "")
            block_language = _canonical_language(language) if isinstance(language,
                                                                         str) and language.strip() else generic_language
            _add_script_part(grouped_parts, block_language, code)

    command_args = _extract_command_args(req)
    cwd = str(req.get("cwd", ""))
    env = dict(req.get("env", {}) or {})
    tool_metadata = dict(req.get("tool_metadata", {}) or {})
    requests: list[ToolScriptScanRequest] = []
    for language, parts in grouped_parts.items():
        requests.append(
            ToolScriptScanRequest(
                script="\n".join(parts),
                language=language,
                command_args=command_args,
                cwd=cwd,
                env=env,
                tool_name=tool_name,
                tool_metadata=tool_metadata,
            ))
    return requests


def _add_script_part(grouped_parts: dict[str, list[str]], language: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    parts = grouped_parts.setdefault(_canonical_language(language), [])
    if value not in parts:
        parts.append(value)


def _extract_tool_name(req: dict[str, Any]) -> str:
    explicit_name = req.get("tool_name")
    if isinstance(explicit_name, str) and explicit_name.strip():
        return explicit_name
    current_tool = get_tool_var()
    current_name = getattr(current_tool, "name", "")
    if isinstance(current_name, str) and current_name.strip():
        return current_name
    return "unknown_tool"


def _extract_explicit_language(req: dict[str, Any]) -> str:
    for key in _LANGUAGE_ARG_KEYS:
        value = req.get(key)
        if isinstance(value, str) and value.strip():
            return _canonical_language(value)
    return ""


def _extract_language(req: dict[str, Any], tool_name: str) -> str:
    explicit_language = _extract_explicit_language(req)
    if explicit_language:
        return explicit_language
    lowered_tool_name = tool_name.lower()
    if "python" in lowered_tool_name:
        return "python"
    if any(hint in lowered_tool_name for hint in ("bash", "shell", "sh")):
        return "bash"
    return "unknown"


def _canonical_language(language: str) -> str:
    normalized = (language or "unknown").strip().lower()
    if normalized in {"py", "python3"}:
        return "python"
    if normalized in {"shell", "sh"}:
        return "bash"
    return normalized


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
