# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""ToolSafetyFilter — BaseFilter that scans tool input before execution."""

from __future__ import annotations

import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools import get_tool_var

from ._audit import AuditLogger
from ._extractors import extract_tool_safety_context
from ._policy import PolicyConfig
from ._scanner import SafetyScanner
from ._telemetry import set_safety_telemetry
from ._types import Decision
from ._types import RiskLevel
from ._types import SafetyReport
from ._types import ScanTarget

_logger = logging.getLogger(__name__)


class ToolSafetyFilter(BaseFilter):
    """Filter that runs a safety scan before tool execution.

    Constructor args:
        policy: PolicyConfig instance (defaults to PolicyConfig.default()).
        audit_path: If set, audit events are written to this JSONL file.
        block_on_review: If True, NEEDS_HUMAN_REVIEW decisions also block
            execution. Default False (only DENY blocks).
    """

    def __init__(self,
                 policy: Optional[PolicyConfig] = None,
                 scanner: Optional[SafetyScanner] = None,
                 audit_path: Optional[str] = None,
                 block_on_review: bool = False) -> None:
        super().__init__()
        self._type = FilterType.TOOL
        self._name = "tool_safety"
        self._scanner = scanner or SafetyScanner(policy or PolicyConfig.default())
        self._audit = AuditLogger(audit_path) if audit_path else None
        self._block_on_review = block_on_review

    async def _before(self, ctx: Any, req: Dict[str, Any], rsp: FilterResult) -> None:
        tool = get_tool_var()
        if tool is None:
            _logger.debug("No tool context available, skipping safety scan")
            return

        scan_req = extract_tool_safety_context(tool, req, target=ScanTarget.TOOL)
        if scan_req is None:
            return  # Not executable content — skip scan

        # Scan — fail-closed: any exception → DENY
        try:
            report = self._scanner.scan(scan_req)
        except Exception:
            report = SafetyReport(
                tool_name=getattr(tool, 'name', 'unknown'),
                decision=Decision.DENY,
                risk_level=RiskLevel.CRITICAL,
                blocked=True,
                sanitized=False,
                duration_ms=0,
                language=scan_req.language,
                target=scan_req.target,
                rule_ids=["SAFETY_SCANNER_ERROR"],
                summary="Safety scanner error — execution blocked.",
                telemetry_attributes={
                    "tool.safety.decision": "deny",
                    "tool.safety.risk_level": "critical",
                    "tool.safety.rule_id": "SAFETY_SCANNER_ERROR",
                },
            )

        # Compute blocking decision first so audit/telemetry record the actual block status
        should_block = (report.decision == Decision.DENY
                        or (report.decision == Decision.NEEDS_HUMAN_REVIEW and self._block_on_review))
        report.set_blocked(should_block)

        # Record audit + telemetry (with correct blocked flag)
        if self._audit:
            self._audit.record(report)
        set_safety_telemetry(report)

        # Block?
        if should_block:
            rsp.rsp = {
                "success": False,
                "error": f"TOOL_SAFETY_BLOCKED: {report.summary}",
                "blocked": True,
                "decision": report.decision.value,
                "return_code": -1,
                "rule_ids": report.rule_ids,
            }
            rsp.is_continue = False


def add_tool_safety_filter(tools: List[Any],
                           policy: Optional[PolicyConfig] = None,
                           audit_path: Optional[str] = None,
                           block_on_review: bool = False) -> None:
    """Attach a fresh ToolSafetyFilter instance to each tool.

    Existing ToolSafetyFilter instances are removed first to prevent
    duplicate filters when called repeatedly (e.g. from SafetyWrappedToolSet).
    Each tool gets its own filter instance to avoid state leakage.
    """
    for tool in tools:
        tool.filters = [f for f in tool.filters if not isinstance(f, ToolSafetyFilter)]
        tool.filters.append(ToolSafetyFilter(policy=policy, audit_path=audit_path, block_on_review=block_on_review))
