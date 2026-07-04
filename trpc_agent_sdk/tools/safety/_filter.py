# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool filter integration for script safety scanning."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools._context_var import get_tool_var

from ._audit import SafetyAuditLogger
from ._audit import build_safety_audit_event
from ._audit import set_safety_span_attributes
from ._policy import SafetyPolicy
from ._policy import resolve_safety_policy
from ._rules import should_block_decision
from ._scanner import SafetyScanner
from ._types import RiskLevel
from ._types import SafetyDecision
from ._types import SafetyReport
from ._types import ScanTarget
from ._types import ScriptLanguage

logger = logging.getLogger(__name__)

_SHELL_TOOL_NAMES = {"workspace_exec", "skill_run", "skill_exec"}
_BLOCKED_ERROR = "Tool execution blocked by safety policy"
_FAIL_CLOSED_ERROR = "Tool safety scan failed closed"


@register_tool_filter("tool_safety_guard")
class ToolSafetyFilter(BaseFilter):
    """Scan tool script-like inputs before the tool handler runs."""
    def __init__(
            self,
            policy_path: str | Path | None = None,
            policy: SafetyPolicy | None = None,
            audit_logger: SafetyAuditLogger | None = None,
            scanner: SafetyScanner | None = None,
    ):
        super().__init__()
        self._policy = resolve_safety_policy(
            scanner=scanner,
            policy=policy,
            policy_path=policy_path,
        )
        self._scanner = scanner or SafetyScanner(self._policy)
        self._audit_logger = audit_logger or SafetyAuditLogger()

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        if not isinstance(req, Mapping):
            return None

        tool_name, tool_description = _current_tool_info()
        target = _build_scan_target(req,
                                    tool_name=tool_name,
                                    tool_description=tool_description)
        if target is None:
            return None

        try:
            report = self._scanner.scan(target)
            if not isinstance(report, SafetyReport):
                raise TypeError(
                    f"SafetyScanner.scan returned {type(report)!r}")
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning(
                "Tool safety scan failed: tool=%s fail_closed=%s error_type=%s",
                tool_name,
                self._policy.fail_closed,
                type(ex).__name__,
            )
            if self._policy.fail_closed:
                report = _fail_closed_report(target, self._policy)
                self._record_report(ctx, report, target)
                _block(rsp, report, _FAIL_CLOSED_ERROR)
            return None

        report = report.model_copy(
            update={
                "blocked": should_block_decision(report.decision, self._policy)
            })
        self._record_report(ctx, report, target)
        if report.blocked:
            _block(rsp, report, _BLOCKED_ERROR)
        return None

    def _record_report(self, ctx: AgentContext, report: SafetyReport,
                       target: ScanTarget) -> None:
        _store_last_report(ctx, report)
        event = build_safety_audit_event(
            report,
            tool_name=target.tool_name,
            cwd=target.cwd,
            function_call_id=_metadata_value(ctx, "function_call_id"),
            agent_name=_metadata_value(ctx, "agent_name"),
        )
        self._audit_logger.emit(event)
        set_safety_span_attributes(report, tool_name=target.tool_name)


def _block(rsp: FilterResult, report: SafetyReport,
           error_message: str) -> None:
    rsp.rsp = {
        "ok": False,
        "blocked": True,
        "error": error_message,
        "safety_report": report.model_dump(mode="json"),
    }
    rsp.error = None
    rsp.is_continue = False


def _build_scan_target(
        req: Mapping[str, Any],
        *,
        tool_name: str,
        tool_description: str,
) -> ScanTarget | None:
    command = _string_value(req, "command")
    content = "\n".join(part for part in (_string_value(req, "script"),
                                          _string_value(req, "code")) if part)
    stdin = _string_value(req, "stdin")
    if not any(value.strip() for value in (command, content, stdin)):
        return None

    language = _language_from_req(req)
    if language == ScriptLanguage.UNKNOWN and tool_name in _SHELL_TOOL_NAMES:
        language = ScriptLanguage.SHELL

    metadata: dict[str, Any] = {}
    if tool_description:
        metadata["tool_description"] = tool_description
    for key in ("background", "tty"):
        if isinstance(req.get(key), bool):
            metadata[key] = req[key]

    return ScanTarget(
        content=content,
        language=language,
        command=command,
        cwd=_string_value(req, "cwd") or _string_value(req, "working_dir"),
        env=_env_keys_only(req.get("env")),
        stdin=stdin,
        timeout_seconds=_timeout_seconds(req),
        tool_name=tool_name,
        tool_metadata=metadata,
    )


def _current_tool_info() -> tuple[str, str]:
    try:
        tool = get_tool_var()
    except Exception:  # pylint: disable=broad-except
        return "", ""
    if tool is None:
        return "", ""
    return str(getattr(tool, "name", "")
               or ""), str(getattr(tool, "description", "") or "")


def _string_value(req: Mapping[str, Any], key: str) -> str:
    value = req.get(key)
    if isinstance(value, str):
        return value
    return ""


def _env_keys_only(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): "" for key in value}


def _timeout_seconds(req: Mapping[str, Any]) -> float | None:
    timeout = _optional_float(req.get("timeout"))
    if timeout is not None:
        return timeout
    return _optional_float(req.get("timeout_sec"))


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _language_from_req(req: Mapping[str, Any]) -> ScriptLanguage:
    value = req.get("language")
    if isinstance(value, ScriptLanguage):
        return value
    if isinstance(value, str):
        try:
            return ScriptLanguage(value.strip().lower())
        except ValueError:
            return ScriptLanguage.UNKNOWN
    return ScriptLanguage.UNKNOWN


def _store_last_report(ctx: AgentContext, report: SafetyReport) -> None:
    dumped = report.model_dump(mode="json")
    try:
        metadata = getattr(ctx, "metadata", None)
        if isinstance(metadata, dict):
            metadata["tool_safety.last_report"] = dumped
            return
    except Exception:  # pylint: disable=broad-except
        pass

    with_metadata = getattr(ctx, "with_metadata", None)
    if callable(with_metadata):
        try:
            with_metadata("tool_safety.last_report", dumped)
        except Exception:  # pylint: disable=broad-except
            pass


def _metadata_value(ctx: AgentContext, key: str) -> str:
    getter = getattr(ctx, "get_metadata", None)
    if callable(getter):
        try:
            value = getter(key, "")
            if isinstance(value, (str, int, float)):
                return str(value)
        except Exception:  # pylint: disable=broad-except
            return ""
    return ""


def _fail_closed_report(target: ScanTarget,
                        policy: SafetyPolicy) -> SafetyReport:
    return SafetyReport(
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.HIGH,
        findings=[],
        elapsed_ms=0.0,
        redacted=False,
        blocked=True,
        language=target.language,
        policy_name=policy.name,
        metadata={"target_tool": target.tool_name} if target.tool_name else {},
    )
