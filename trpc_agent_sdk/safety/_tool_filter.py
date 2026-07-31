# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Filter that blocks explicit script fields before real Tool execution."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import get_tool_var

from ._extractors import ToolRequestExtractor
from ._models import SafetyDecision
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._scanner import SafetyScanner


def blocked_envelope(report: SafetyReport) -> dict[str, Any]:
    """Return the minimal model-visible block response without evidence."""
    return {
        "safety": {
            "schema_version": report.schema_version,
            "decision": report.decision.value,
            "execution_blocked": report.execution_blocked,
            "risk_level": report.risk_level.value if report.risk_level else None,
            "rule_ids": list(report.rule_ids),
            "failure_code": report.failure_code,
            "recommendation": "Review the safety policy and requested operation.",
        }
    }


class ToolSafetyFilter(BaseFilter):
    """Scan repaired Tool args via an explicit extractor before ``handle``."""

    def __init__(self, scanner: SafetyScanner, extractor: ToolRequestExtractor):
        super().__init__()
        self._scanner = scanner
        self._extractor = extractor
        self._type = FilterType.TOOL
        self._name = "tool_script_safety"

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        if not isinstance(req, dict):
            return
        tool = get_tool_var()
        try:
            invocation_context = get_invocation_ctx()
        except (LookupError, RuntimeError):
            invocation_context = None
        try:
            extracted = self._extractor(tool, req, invocation_context)
        except Exception:  # pylint: disable=broad-except
            request = SafetyScanRequest(
                script="",
                language="unsupported-extractor",
                tool_name=getattr(tool, "name", None),
                source_type="tool",
            )
            report = self._scanner.scan(request)
            rsp.rsp = blocked_envelope(report)
            rsp.is_continue = False
            return
        if extracted is None:
            return
        requests = extracted if isinstance(extracted, tuple) else (extracted, )
        reports = [self._scanner.scan(item) for item in requests]
        blocked = next((item for item in reports if item.decision is SafetyDecision.DENY), None)
        if blocked is None:
            blocked = next(
                (item for item in reports if item.decision is SafetyDecision.NEEDS_HUMAN_REVIEW),
                None,
            )
        if blocked is not None:
            rsp.rsp = blocked_envelope(blocked)
            rsp.is_continue = False
