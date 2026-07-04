# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Audit and tracing helpers for tool script safety reports."""

from __future__ import annotations

import logging
from pathlib import Path

from opentelemetry import trace

from ._types import SafetyAuditEvent
from ._types import SafetyReport

logger = logging.getLogger(__name__)


def _ordered_rule_ids(report: SafetyReport) -> list[str]:
    return list(dict.fromkeys(finding.rule_id for finding in report.findings))


def build_safety_audit_event(
        report: SafetyReport,
        *,
        tool_name: str = "",
        cwd: str = "",
        function_call_id: str = "",
        agent_name: str = "",
) -> SafetyAuditEvent:
    """Build an audit-safe summary event from a safety report."""

    resolved_tool_name = tool_name or str(
        report.metadata.get("target_tool", "") or "")
    return SafetyAuditEvent(
        tool_name=resolved_tool_name,
        decision=report.decision,
        risk_level=report.risk_level,
        rule_ids=_ordered_rule_ids(report),
        elapsed_ms=report.elapsed_ms,
        redacted=report.redacted,
        blocked=report.blocked,
        language=report.language,
        cwd=cwd,
        function_call_id=function_call_id,
        agent_name=agent_name,
        policy_name=report.policy_name,
        scanner_version=report.scanner_version,
        finding_count=len(report.findings),
    )


class SafetyAuditLogger:
    """Optional JSONL writer for safety audit events."""
    def __init__(self, path: str | Path | None = None, enabled: bool = True):
        self.path = Path(path) if path is not None else None
        self.enabled = enabled

    def emit(self, event: SafetyAuditEvent) -> None:
        """Append an audit event to JSONL when explicitly configured."""

        if not self.enabled or self.path is None:
            return

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as audit_file:
                audit_file.write(event.model_dump_json())
                audit_file.write("\n")
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to write safety audit event: %s", ex)


def set_safety_span_attributes(report: SafetyReport, *,
                               tool_name: str = "") -> None:
    """Best-effort write of safety summary fields onto the current OTel span."""

    rule_ids = _ordered_rule_ids(report)
    resolved_tool_name = tool_name or str(
        report.metadata.get("target_tool", "") or "")
    attributes: dict[str, str | bool | int | float] = {
        "tool.safety.decision": report.decision.value,
        "tool.safety.risk_level": report.risk_level.value,
        "tool.safety.rule_ids": ",".join(rule_ids),
        "tool.safety.blocked": report.blocked,
        "tool.safety.redacted": report.redacted,
        "tool.safety.elapsed_ms": report.elapsed_ms,
        "tool.safety.finding_count": len(report.findings),
        "tool.safety.policy_name": report.policy_name,
        "tool.safety.scanner_version": report.scanner_version,
        "tool.safety.language": report.language.value,
    }
    if resolved_tool_name:
        attributes["tool.safety.tool_name"] = resolved_tool_name

    try:
        span = trace.get_current_span()
        for key, value in attributes.items():
            span.set_attribute(key, value)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Failed to set safety span attributes: %s", ex)
