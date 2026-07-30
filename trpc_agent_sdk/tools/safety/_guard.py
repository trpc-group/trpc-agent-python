#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Fail-closed orchestration shared by safety integrations."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from ._audit import LoggingAuditSink
from ._audit import SafetyAuditSink
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyAuditEvent
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import ScriptScanRequest
from ._scanner import ToolScriptSafetyScanner
from ._telemetry import record_safety_attributes


class ToolSafetyGuard:
    """Scan, audit, and return a decision before execution."""

    def __init__(
        self,
        scanner: ToolScriptSafetyScanner | None = None,
        audit_sink: SafetyAuditSink | None = None,
    ):
        self.scanner = scanner or ToolScriptSafetyScanner()
        self.audit_sink = audit_sink or LoggingAuditSink()

    def check(self, request: ScriptScanRequest) -> SafetyReport:
        """Return an audited report; scanner/audit failures deny execution."""

        try:
            report = self.scanner.scan(request)
        except Exception as exc:  # pylint: disable=broad-except
            report = self.scanner.failure_report(exc)

        return self.record(request.metadata.name, report)

    def record(self, tool_name: str, report: SafetyReport) -> SafetyReport:
        """Record an existing report, failing closed when auditing fails."""

        try:
            self.audit_sink.emit(self._audit_event(tool_name, report))
        except Exception as exc:  # pylint: disable=broad-except
            report = self._audit_failure_report(exc)
        record_safety_attributes(report)
        return report

    @staticmethod
    def _audit_event(tool_name: str, report: SafetyReport) -> SafetyAuditEvent:
        return SafetyAuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            tool_name=tool_name,
            decision=report.decision,
            risk_level=report.risk_level,
            rule_ids=report.rule_ids,
            duration_ms=report.duration_ms,
            redacted=report.redacted,
            execution_blocked=report.blocked,
            policy_version=report.policy_version,
        )

    def _audit_failure_report(self, error: Exception) -> SafetyReport:
        finding = SafetyFinding(
            category=RiskCategory.SCAN_ERROR,
            risk_level=RiskLevel.HIGH,
            rule_id="AUDIT_WRITE_ERROR",
            evidence=f"audit sink failed with {type(error).__name__}",
            recommendation="Restore the required audit sink before retrying execution.",
            decision=SafetyDecision.DENY,
        )
        return SafetyReport(
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.HIGH,
            findings=[finding],
            duration_ms=0,
            redacted=True,
            summary="deny: required audit event could not be persisted",
            policy_version=self.scanner.policy.version,
        )
