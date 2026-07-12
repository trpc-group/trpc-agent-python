# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Composition root for scanning, audit output, and telemetry."""

from __future__ import annotations

import hashlib
from typing import Any
from typing import Iterable
from typing import Optional

from ._audit import AuditTarget
from ._audit import record_audit_event
from ._models import DECISION_ORDER
from ._models import RISK_LEVEL_ORDER
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyAuditEvent
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._policy import ToolSafetyPolicy
from ._telemetry import trace_safety_report


class ToolSafetyGuard:
    """Run static scans and emit one redacted decision record per execution."""

    def __init__(
        self,
        policy: Optional[ToolSafetyPolicy] = None,
        *,
        scanner: Any = None,
        audit_sink: Optional[AuditTarget] = None,
    ) -> None:
        if policy is None:
            policy = getattr(scanner, "policy", None) or ToolSafetyPolicy()
        self.policy = policy
        if scanner is None:
            from ._scanner import ToolSafetyScanner
            scanner = ToolSafetyScanner(policy)
        self.scanner = scanner
        self.audit_sink = audit_sink

    def scan(self, request: SafetyScanRequest, *, record: bool = True) -> SafetyReport:
        """Scan one request and optionally emit its final decision."""

        report = self.scanner.scan(request)
        if not isinstance(report, SafetyReport):
            raise TypeError("tool safety scanner must return SafetyReport")
        expected_blocked = report.decision is SafetyDecision.DENY or (
            report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW and self.policy.block_on_review)
        if report.blocked != expected_blocked:
            report = report.model_copy(update={"blocked": expected_blocked})
        if record:
            self.record(report)
        return report

    def failure_report(
        self,
        *,
        tool_name: str,
        error: Exception,
        request: Optional[SafetyScanRequest] = None,
        rule_id: str = "SCAN-ERROR",
        record: bool = True,
    ) -> SafetyReport:
        """Build a redacted fail-closed report for scanner infrastructure errors."""

        language = request.language if request is not None else ScriptLanguage.BASH
        script = request.script if request is not None else ""
        finding = SafetyFinding(
            rule_id=rule_id,
            category=RiskCategory.SCAN_ERROR,
            risk_level=RiskLevel.HIGH,
            decision=SafetyDecision.DENY,
            evidence=f"tool safety processing failed with {type(error).__name__}",
            recommendation="Correct the request or scanner failure before retrying execution.",
            metadata={"error_type": type(error).__name__},
        )
        report = SafetyReport(
            tool_name=tool_name,
            language=language,
            languages=[language],
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.HIGH,
            findings=[finding],
            rule_id=rule_id,
            rule_ids=[rule_id],
            duration_ms=0,
            script_sha256=hashlib.sha256(script.encode("utf-8", errors="replace")).hexdigest(),
            policy_version=self.policy.version,
            redacted=True,
            blocked=True,
        )
        if record:
            self.record(report)
        return report

    def finalize_review(
        self,
        report: SafetyReport,
        approved: bool,
        *,
        record: bool = True,
    ) -> SafetyReport:
        """Resolve a review report, optionally recording only the resolution."""

        if report.decision is not SafetyDecision.NEEDS_HUMAN_REVIEW:
            raise ValueError("only needs_human_review reports can be finalized")
        if type(approved) is not bool:  # pylint: disable=unidiomatic-typecheck
            raise TypeError("approved must be bool")
        finalized = report.model_copy(
            update={
                "decision": SafetyDecision.ALLOW if approved else SafetyDecision.DENY,
                "blocked": not approved,
                "human_review_approved": approved,
            })
        if record:
            self.record(finalized)
        return finalized

    def merge_reports(self, reports: Iterable[SafetyReport]) -> SafetyReport:
        """Aggregate multiple executable payload scans into one tool decision."""

        report_list = list(reports)
        if not report_list:
            raise ValueError("at least one safety report is required")
        if len(report_list) == 1:
            return report_list[0]
        policy_versions = {report.policy_version for report in report_list}
        tool_names = {report.tool_name for report in report_list}
        if len(policy_versions) != 1 or len(tool_names) != 1:
            raise ValueError("safety reports must share one policy version and tool name")

        decision = max((report.decision for report in report_list), key=DECISION_ORDER.__getitem__)
        risk_level = max((report.risk_level for report in report_list), key=RISK_LEVEL_ORDER.__getitem__)
        findings = []
        rule_ids = []
        for report in report_list:
            findings.extend(report.findings)
            rule_ids.extend(report.rule_ids)
        rule_ids = list(dict.fromkeys(rule_ids))
        primary_candidates = [report for report in report_list if report.decision is decision]
        primary = max(primary_candidates, key=lambda report: RISK_LEVEL_ORDER[report.risk_level])
        languages = list(dict.fromkeys(report.language for report in report_list))
        digest_input = "\0".join(report.script_sha256 for report in report_list).encode("ascii")
        blocked = decision is SafetyDecision.DENY or (decision is SafetyDecision.NEEDS_HUMAN_REVIEW
                                                      and self.policy.block_on_review)
        return SafetyReport(
            tool_name=report_list[0].tool_name,
            language=languages[0] if languages else ScriptLanguage.BASH,
            languages=languages,
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            rule_id=primary.rule_id,
            rule_ids=rule_ids,
            duration_ms=sum(report.duration_ms for report in report_list),
            script_sha256=hashlib.sha256(digest_input).hexdigest(),
            policy_version=report_list[0].policy_version,
            redacted=all(report.redacted for report in report_list),
            blocked=blocked,
            scanned_at=min(report.scanned_at for report in report_list),
        )

    def record(self, report: SafetyReport) -> SafetyAuditEvent:
        """Emit span attributes and a compact audit event on a best-effort basis."""

        trace_safety_report(report)
        primary_rule_id = getattr(report, "rule_id", None) or (report.rule_ids[0] if report.rule_ids else None)
        event = SafetyAuditEvent(
            timestamp=report.scanned_at,
            tool_name=report.tool_name,
            decision=report.decision,
            risk_level=report.risk_level,
            rule_id=primary_rule_id,
            rule_ids=list(report.rule_ids),
            duration_ms=report.duration_ms,
            redacted=report.redacted,
            blocked=report.blocked,
            human_review_approved=report.human_review_approved,
            script_sha256=report.script_sha256,
            policy_version=report.policy_version,
        )
        record_audit_event(self.audit_sink, event)
        return event
