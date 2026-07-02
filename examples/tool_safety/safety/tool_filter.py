# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""ToolSafetyFilter: a TRPC Agent Tool Filter that runs the safety scanner
before tool execution.

When the scanner returns DENY, the filter sets ``is_continue=False`` and the
tool's ``_run_async_impl`` is never called. An audit record is always written,
and OpenTelemetry span attributes are set on the current span.

Usage::

    from examples.tool_safety.safety.tool_filter import ToolSafetyFilter
    from examples.tool_safety.safety.policy import PolicyConfig

    policy = PolicyConfig.from_yaml("examples/tool_safety/tool_safety_policy.yaml")
    safety_filter = ToolSafetyFilter(policy=policy)
    tool = BashTool(filters=[safety_filter])
"""
from __future__ import annotations

from typing import Any
from typing import Optional

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterType

from .audit import AuditLogger
from .policy import PolicyConfig
from .scanner import SafetyScanner
from .types import Decision
from .types import ScanInput


# Argument keys (in priority order) that may carry a script/command to scan.
_SCRIPT_ARG_KEYS = ("command", "script", "code", "cmd", "bash", "shell_command")
_LANGUAGE_ARG_KEYS = ("language", "lang")
_WORKDIR_ARG_KEYS = ("cwd", "workdir", "working_dir")


class ToolSafetyFilter(BaseFilter):
    """Pre-execution safety filter for Tool / Skill / CodeExecutor scripts."""

    def __init__(
        self,
        policy: PolicyConfig,
        *,
        audit_path: Optional[str] = None,
        tool_name: str = "tool_safety_filter",
    ):
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_safety_filter"
        self.policy = policy
        self.scanner = SafetyScanner(policy=policy)
        self.audit = AuditLogger(audit_path)
        self._configured_tool_name = tool_name

    async def _before(self, ctx: Any, req: Any, rsp: FilterResult) -> None:
        """Scan the tool args; block execution when decision is DENY."""
        args = req if isinstance(req, dict) else {}
        script = _extract_script(args)
        if script is None or not script.strip():
            # Nothing to scan: allow.
            return

        tool_name = self._resolve_tool_name(ctx)
        scan_input = ScanInput(
            script=script,
            language=_extract_language(args),
            workdir=_extract_workdir(args),
            env=_extract_env(args),
            args=_extract_args_list(args),
            tool_name=tool_name,
        )
        report = self.scanner.scan(scan_input)

        intercepted = report.decision == Decision.DENY
        self.audit.log(report, intercepted=intercepted)

        if report.decision == Decision.DENY:
            rsp.rsp = {
                "error": "TOOL_SAFETY_DENY",
                "decision": report.decision.value,
                "risk_level": report.risk_level.value,
                "rule_ids": report.rule_ids,
                "findings": [f"  - {f.rule_id}: {f.evidence}" for f in report.findings],
                "recommendation": "Review the flagged patterns; see audit log for details.",
            }
            rsp.is_continue = False
            rsp.error = None
            return

        if report.decision == Decision.NEEDS_HUMAN_REVIEW:
            # Allow but annotate: human review required.
            rsp.rsp = {
                "warning": "TOOL_SAFETY_NEEDS_REVIEW",
                "risk_level": report.risk_level.value,
                "rule_ids": report.rule_ids,
            }
            return

    def _resolve_tool_name(self, ctx: Any) -> str:
        """Best-effort fetch the current tool name from context var."""
        try:
            from trpc_agent_sdk.tools import get_tool_var
            tool = get_tool_var()
            if tool is not None and getattr(tool, "name", None):
                return tool.name
        except Exception:  # pylint: disable=broad-expect
            pass
        return self._configured_tool_name


# ---------------------------------------------------------------------------
# Argument extraction helpers
# ---------------------------------------------------------------------------


def _extract_script(args: dict[str, Any]) -> Optional[str]:
    """Pull the script/command/code string from common tool arg shapes."""
    for key in _SCRIPT_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # Code executor shape: code_blocks list
    code_blocks = args.get("code_blocks")
    if isinstance(code_blocks, list):
        parts = []
        for blk in code_blocks:
            if isinstance(blk, dict):
                parts.append(blk.get("code", ""))
            elif hasattr(blk, "code"):
                parts.append(blk.code)
        if parts:
            return "\n".join(parts)
    return None


def _extract_language(args: dict[str, Any]) -> str:
    for key in _LANGUAGE_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.lower()
    # Infer from script key presence
    if "command" in args or "bash" in args or "cmd" in args:
        return "bash"
    if "code" in args:
        return "python"
    return ""


def _extract_workdir(args: dict[str, Any]) -> Optional[str]:
    for key in _WORKDIR_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _extract_env(args: dict[str, Any]) -> Optional[dict[str, str]]:
    val = args.get("env")
    if isinstance(val, dict):
        return {str(k): str(v) for k, v in val.items()}
    return None


def _extract_args_list(args: dict[str, Any]) -> Optional[list[str]]:
    val = args.get("args")
    if isinstance(val, list):
        return [str(v) for v in val]
    return None
