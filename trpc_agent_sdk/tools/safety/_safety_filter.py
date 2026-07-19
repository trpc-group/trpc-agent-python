# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Integration of the Safety Guard as a tRPC-Agent Filter.

This module provides a :class:`ToolSafetyFilter` that plugs into the existing
tRPC-Agent filter pipeline. When registered as a tool filter, it intercepts
tool execution requests **before** the actual tool runs, scans the script
content, and blocks execution if the decision is ``DENY``.

Registration (using the framework's filter registry)::

    from trpc_agent_sdk.filter import register_tool_filter, FilterType
    from trpc_agent_sdk.tools.safety import ToolSafetyFilter

    @register_tool_filter("tool_safety")
    class MyToolSafetyFilter(ToolSafetyFilter):
        pass

Per-tool usage (inline)::

    from trpc_agent_sdk.tools.safety import ToolSafetyFilter
    from trpc_agent_sdk.tools import FunctionTool

    tool = FunctionTool(
        name="my_tool",
        description="...",
        filters=[ToolSafetyFilter()],
    )
"""

from __future__ import annotations

from typing import Any
from typing import Optional

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger

from ._audit import AuditLogger
from ._policy import SafetyPolicy
from ._policy import get_policy
from ._scanner import SafetyScanner
from ._telemetry import set_safety_span_attributes
from ._types import Decision
from ._types import SafetyScanInput
from ._types import ScriptType


class ToolSafetyFilter(BaseFilter):
    """A tRPC-Agent filter that scans tool scripts for safety before execution.

    Implements the ``_before`` hook to inspect tool arguments for script-like
    content (e.g. ``code``, ``script``, ``command`` fields) and runs the
    safety scanner on them.

    When the scanner returns ``DENY`` the filter sets ``is_continue = False``
    on the ``FilterResult``, which prevents the tool from executing.

    Args:
        policy: Optional policy override. Uses the default if not provided.
        audit_log_path: Path to write audit events (JSONL). If omitted,
                        events are only emitted via the logger.
        block_on_deny: If True (default), the filter prevents execution when
                       the decision is DENY.
        block_on_review: If True, also block on NEEDS_HUMAN_REVIEW.
                         Defaults to False — callers should check
                         ``rsp.safety_report`` to implement a human-
                         review gate.
    """

    def __init__(
        self,
        *,
        policy: Optional[SafetyPolicy] = None,
        audit_log_path: Optional[str] = None,
        block_on_deny: bool = True,
        block_on_review: bool = False,
    ) -> None:
        super().__init__()
        self._policy = policy or get_policy()
        self._scanner = SafetyScanner(self._policy)
        self._audit = AuditLogger(audit_log_path)
        self._block_on_deny = block_on_deny
        self._block_on_review = block_on_review

        # Identify ourselves within the filter chain
        from trpc_agent_sdk.abc import FilterType
        self._type = FilterType.TOOL
        self._name = "tool_safety"

    # ------------------------------------------------------------------
    # Filter hooks
    # ------------------------------------------------------------------

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Scan the incoming tool request before execution.

        Args:
            ctx: Agent execution context.
            req: The tool request dictionary / object.
            rsp: Mutable filter result — we write an error to it on DENY.
        """
        script_content = _extract_script_content(req)
        if not script_content:
            # No script-like content found — nothing to scan.
            return

        script_type = _guess_script_type(req, script_content)
        tool_name = _extract_tool_name(req)

        scan_input = SafetyScanInput(
            script_content=script_content,
            script_type=script_type,
            tool_name=tool_name,
        )

        report = self._scanner.scan(scan_input)

        # Always audit
        self._audit.log_event(report)

        # Always set OTel attributes (no-op if OTel not installed)
        set_safety_span_attributes(report)

        # Always expose the report for downstream inspection
        setattr(rsp, "safety_report", report)

        if report.decision == Decision.DENY:
            logger.warning(
                "ToolSafetyFilter BLOCKED tool '%s': %s",
                tool_name,
                report.summary,
            )
            if self._block_on_deny:
                rsp.error = ToolSafetyDeniedError(report)
                rsp.is_continue = False

        elif report.decision == Decision.NEEDS_HUMAN_REVIEW:
            logger.info(
                "ToolSafetyFilter flagged tool '%s' for human review: %s",
                tool_name,
                report.summary,
            )
            if self._block_on_review:
                rsp.error = ToolSafetyDeniedError(report)
                rsp.is_continue = False

        else:
            logger.debug("ToolSafetyFilter allowed tool '%s'.", tool_name)


class ToolSafetyDeniedError(RuntimeError):
    """Raised (or attached to FilterResult) when a tool is blocked by the safety filter."""

    def __init__(self, report):
        self.report = report
        super().__init__(report.summary)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_script_content(req: Any) -> Optional[str]:
    """Heuristically extract script-like content from a tool request.

    All recognised script-bearing fields are collected and joined so that
    a request carrying both ``code`` (benign) and ``command`` (dangerous)
    does not bypass detection by hiding behind the first hit.
    """
    if isinstance(req, str):
        return req
    parts: list[str] = []
    seen: set[str] = set()

    def _collect(val: Any) -> None:
        if isinstance(val, str) and val.strip():
            stripped = val.strip()
            if stripped not in seen:
                parts.append(stripped)
                seen.add(stripped)

    if isinstance(req, dict):
        for key in ("code", "script", "command", "cmd", "shell", "source", "content", "text", "input"):
            _collect(req.get(key))
        args = req.get("args", {})
        if isinstance(args, dict):
            for key in ("code", "script", "command", "cmd", "shell"):
                _collect(args.get(key))
        kwargs = req.get("kwargs")
        if isinstance(kwargs, dict) and kwargs:
            sub = _extract_script_content(kwargs)
            if sub:
                _collect(sub)
    if hasattr(req, "args") and isinstance(getattr(req, "args"), dict):
        sub = _extract_script_content(getattr(req, "args"))
        if sub:
            _collect(sub)
    if hasattr(req, "script_content"):
        _collect(getattr(req, "script_content"))

    return "\n".join(parts) if parts else None


def _guess_script_type(req: Any, script: str) -> ScriptType:
    """Guess script type from request metadata or content."""
    # Check explicit hints first
    if isinstance(req, dict):
        hint = req.get("script_type") or req.get("language")
        if hint:
            hint_lower = str(hint).lower()
            if "python" in hint_lower:
                return ScriptType.PYTHON
            if hint_lower in ("bash", "sh", "shell"):
                return ScriptType.BASH
    if hasattr(req, "script_type"):
        hint = str(getattr(req, "script_type", "")).lower()
        if "python" in hint:
            return ScriptType.PYTHON
        if hint in ("bash", "sh", "shell"):
            return ScriptType.BASH

    # Fall back to content heuristics
    return SafetyScanner._detect_type(script)


def _extract_tool_name(req: Any) -> str:
    """Extract a human-readable tool name from the request."""
    if isinstance(req, dict):
        return req.get("tool_name") or req.get("name") or req.get("tool") or "unknown"
    if hasattr(req, "tool_name"):
        return str(getattr(req, "tool_name", "unknown"))
    if hasattr(req, "name"):
        return str(getattr(req, "name", "unknown"))
    return "unknown"
