# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool filter that scans scripts *before* they are executed.

``ToolSafetyGuardFilter`` plugs into the framework's existing filter chain
(``@register_tool_filter``) with zero core intrusion. Attach it to any tool
whose arguments carry a script/command (e.g. ``BashTool``) via ``filters_name``
or ``filters``. The scan runs in ``_before`` so the base filter lifecycle
applies it on **both** the blocking (``run``) and streaming (``run_stream``)
paths -- a streaming tool must not bypass the guard. On a blocking verdict it
short-circuits the chain (``FilterResult(is_continue=False)``) so the
underlying tool never runs.

Registered under the name ``"tool_safety_guard"``, so it can be attached as::

    BashTool(..., filters_name=["tool_safety_guard"])
"""

from __future__ import annotations

from typing import Any
from typing import Optional

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.filter import register_tool_filter
from trpc_agent_sdk.tools import get_tool_var

from ._audit import SafetyAuditLogger
from ._scanner import SafetyScanner
from ._scanner import _mask_secrets
from ._types import SafetyDecision
from ._types import ScanInput
from ._types import ScanReport
from ._types import ScriptLanguage

# Argument keys that may carry an executable script, in priority order.
_SCRIPT_ARG_KEYS = ("command", "cmd", "script", "code")
# Argument keys that imply a shell command rather than a Python script.
_SHELL_ARG_KEYS = ("command", "cmd")


def _coerce_script(value: Any) -> Optional[str]:
    """Coerce a script-carrying argument to scannable text.

    A tool may pass a command as a plain string or as an argv-style
    ``list``/``tuple`` (e.g. ``["rm", "-rf", "/"]``). The latter must not slip
    past the gate unscanned, so it is joined into a command line. Any other
    non-empty value is still stringified and scanned rather than silently
    allowed, so a command smuggled inside a container (``{"real": "rm -rf /"}``)
    is still surfaced to the regex layer. ``None`` / empty means "no script".
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(part) for part in value)
        return joined if joined.strip() else None
    text = str(value)
    return text if text.strip() else None


@register_tool_filter("tool_safety_guard")
class ToolSafetyGuardFilter(BaseFilter):
    """Scan tool arguments and block execution of high-risk scripts."""

    def __init__(
        self,
        scanner: Optional[SafetyScanner] = None,
        audit_logger: Optional[SafetyAuditLogger] = None,
        *,
        block_on_review: bool = True,
    ) -> None:
        """Create the guard filter.

        Args:
            scanner: The scanner to use. Defaults to a scanner with the bundled
                default policy, so the registered singleton works out of the box.
            audit_logger: Where to record decisions. Defaults to an in-memory
                (non-persisting) logger.
            block_on_review: Whether ``needs_human_review`` also blocks
                execution. Defaults to True (fail-safe); set False to only block
                outright denials.
        """
        super().__init__()
        self._scanner = scanner or SafetyScanner()
        self._audit = audit_logger or SafetyAuditLogger()
        self._block_on_review = block_on_review

    @property
    def scanner(self) -> SafetyScanner:
        """Return the underlying scanner."""
        return self._scanner

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Scan the request before execution; block by short-circuiting the chain.

        Implemented as ``_before`` (rather than overriding ``run``) so the base
        filter runs it for both ``run`` and ``run_stream``: neither the blocking
        nor the streaming invocation path can slip past the guard. A blocking
        verdict sets ``rsp.is_continue = False`` and stores the deny response,
        which both base lifecycles honour by returning without calling the tool.
        """
        scan_input = self._build_scan_input(req)
        if scan_input is None or not scan_input.script.strip():
            # Nothing scriptable to inspect; let the tool run.
            return

        report = self._scanner.scan(scan_input)
        blocked = self._is_blocking(report.decision)
        await self._audit.arecord(report, blocked=blocked)

        if blocked:
            rsp.rsp = self._deny_response(report)
            rsp.is_continue = False

    # -- helpers ---------------------------------------------------------------
    def _is_blocking(self, decision: SafetyDecision) -> bool:
        """Whether a decision should prevent execution under this filter."""
        if decision is SafetyDecision.DENY:
            return True
        if decision is SafetyDecision.NEEDS_HUMAN_REVIEW:
            return self._block_on_review
        return False

    @staticmethod
    def _build_scan_input(req: Any) -> Optional[ScanInput]:
        """Extract a :class:`ScanInput` from the tool's argument dict."""
        if not isinstance(req, dict):
            return None
        script: Optional[str] = None
        used_key: Optional[str] = None
        for key in _SCRIPT_ARG_KEYS:
            coerced = _coerce_script(req.get(key))
            if coerced is not None:
                script = coerced
                used_key = key
                break
        if script is None:
            return None

        tool = get_tool_var()
        tool_name = getattr(tool, "name", "<unknown>")

        declared = req.get("language")
        if declared:
            language = ScriptLanguage.from_str(declared)
        elif used_key in _SHELL_ARG_KEYS or tool_name.lower() in ("bash", "shell", "sh"):
            language = ScriptLanguage.BASH
        else:
            language = ScriptLanguage.UNKNOWN

        return ScanInput(script=script, language=language, tool_name=tool_name)

    @staticmethod
    def _deny_response(report: ScanReport) -> dict[str, Any]:
        """Build the tool-style response returned when execution is blocked."""
        rule_ids = ", ".join(report.rule_ids()) or "<none>"
        safety_report = report.model_dump(mode="json")
        # Defence in depth: this response is echoed back to the caller/model, so
        # secret-looking evidence is always masked here even when the operator
        # has disabled the policy's global redaction -- a blocked script must
        # never leak a credential through this channel.
        for hit in safety_report.get("hits", []):
            evidence = hit.get("evidence")
            if isinstance(evidence, str):
                hit["evidence"] = _mask_secrets(evidence)
        return {
            "success": False,
            "error": (f"SAFETY_BLOCKED [{report.decision.value}]: {report.summary} "
                      f"Triggered rules: {rule_ids}."),
            "safety_decision": report.decision.value,
            "safety_risk_level": report.risk_level.value,
            "safety_report": safety_report,
        }
