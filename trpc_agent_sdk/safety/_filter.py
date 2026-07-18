# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""ToolSafetyFilter: pre-execution safety filter for Tool / Skill scripts."""
from __future__ import annotations

import contextvars
from typing import Any
from typing import Optional

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterType

from ._audit import AuditLogger
from ._policy import PolicyConfig
from ._scanner import SafetyScanner
from ._types import Decision
from ._types import SafetyReport
from ._types import ScanInput

_SCRIPT_ARG_KEYS = ("command", "script", "code", "cmd", "bash", "shell_command")
_LANGUAGE_ARG_KEYS = ("language", "lang")
_WORKDIR_ARG_KEYS = ("cwd", "workdir", "working_dir")

# ContextVar carries the non-blocking review report from _before to _after.
# We cannot stash it on the FilterResult because BaseFilter.run may replace
# the whole result object with the one returned by handle(). ContextVar is
# async-safe: each task sees its own value across awaits.
_REVIEW_REPORT: contextvars.ContextVar[Optional[SafetyReport]] = contextvars.ContextVar(
    "_safety_review_report", default=None
)


class ToolSafetyFilter(BaseFilter):
    """Pre-execution safety filter for Tool / Skill / CodeExecutor scripts.

    When the scanner returns DENY (or NEEDS_HUMAN_REVIEW with
    ``policy.block_on_review``), the filter sets ``is_continue=False`` so the
    tool's ``_run_async_impl`` is never called. An audit record is always
    written, and OpenTelemetry span attributes are set on the current span.

    For non-blocking NEEDS_HUMAN_REVIEW (``block_on_review=False``), the tool
    is allowed to run, and the warning is merged into the tool's result dict
    (keys ``safety_warning`` / ``safety_risk_level`` / ``safety_rule_ids``) in
    ``_after`` so the caller actually sees it. This is necessary because
    ``BaseFilter.run`` overwrites ``result.rsp`` with the tool's return value
    after ``_before`` returns.
    """

    def __init__(
        self,
        policy: PolicyConfig,
        *,
        audit_path: Optional[str] = None,
        tool_name: str = "tool_safety_filter",
        block_on_review: Optional[bool] = None,
    ):
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_safety_filter"
        self.policy = policy
        self.scanner = SafetyScanner(policy=policy)
        self.audit = AuditLogger(audit_path)
        self._configured_tool_name = tool_name
        self._block_on_review = (policy.block_on_review if block_on_review is None else block_on_review)
        # Prefer lightweight context-var import; never pull trpc_agent_sdk.tools
        # package (heavy optional deps like anthropic) on every scan.
        self._get_tool_var = None
        try:
            from trpc_agent_sdk.tools._context_var import get_tool_var
            self._get_tool_var = get_tool_var
        except Exception:  # pylint: disable=broad-except
            self._get_tool_var = None

    async def _before(self, ctx: Any, req: Any, rsp: FilterResult) -> None:
        """Scan the tool args; block execution when decision is DENY."""
        args = req if isinstance(req, dict) else {}
        script = _extract_script(args)
        if script is None or not script.strip():
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

        should_block = report.decision == Decision.DENY or (report.decision == Decision.NEEDS_HUMAN_REVIEW
                                                            and self._block_on_review)
        self.audit.log(report, intercepted=should_block)

        if should_block:
            error_code = ("TOOL_SAFETY_DENY" if report.decision == Decision.DENY else "TOOL_SAFETY_NEEDS_REVIEW")
            # Align with BashTool / tool return schema: success + error + command.
            # IMPORTANT: FilterResult.error must be Exception|None. Putting a
            # string there makes run_filters raise TypeError. Stop the chain
            # with is_continue=False and keep error=None so rsp.rsp is returned.
            command = script if isinstance(script, str) else ""
            rsp.rsp = {
                "success": False,
                "error": error_code,
                "command": command,
                "return_code": -1,
                "stdout": "",
                "stderr": error_code,
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
            # Non-blocking review: stash the report via ContextVar so _after
            # (run after the tool executes) can merge the warning into the
            # tool's actual result. Setting rsp.rsp here is useless because
            # BaseFilter.run's handle() step overwrites result.rsp with the
            # tool's return value.
            _REVIEW_REPORT.set(report)

    async def _after(self, ctx: Any, req: Any, rsp: FilterResult) -> None:
        """Merge non-blocking safety review warnings into the tool result.

        ``_before`` cannot attach the warning to ``rsp.rsp`` because
        ``BaseFilter.run`` overwrites ``result.rsp`` with the tool's return
        value in the handle() step. We instead stash the report in a
        ContextVar during ``_before`` and merge the warning fields here,
        after the tool has run, so the caller actually sees them.
        """
        report = _REVIEW_REPORT.get()
        if report is None:
            return
        # Clear the stash so it does not leak to the next call in this context.
        _REVIEW_REPORT.set(None)
        if not isinstance(rsp.rsp, dict):
            # Wrap non-dict results so we can attach warning fields without
            # losing the original payload.
            rsp.rsp = {"result": rsp.rsp}
        rsp.rsp["safety_warning"] = "TOOL_SAFETY_NEEDS_REVIEW"
        rsp.rsp["safety_risk_level"] = report.risk_level.value
        rsp.rsp["safety_rule_ids"] = list(report.rule_ids)

    def _resolve_tool_name(self, ctx: Any) -> str:
        get_tool_var = self._get_tool_var
        if get_tool_var is None:
            return self._configured_tool_name
        try:
            tool = get_tool_var()
            if tool is not None and getattr(tool, "name", None):
                return tool.name
        except Exception:  # pylint: disable=broad-except
            pass
        return self._configured_tool_name


def _extract_script(args: dict[str, Any]) -> Optional[str]:
    for key in _SCRIPT_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
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
