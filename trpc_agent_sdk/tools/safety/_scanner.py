# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool script safety scan orchestration."""

from __future__ import annotations

import time
from typing import Any

from ._bash_rules import nested_payloads
from ._bash_rules import scan_bash
from ._bash_rules import stdin_language
from ._common_rules import scan_limits
from ._common_rules import scan_paths
from ._models import DECISION_PRIORITY
from ._models import RISK_PRIORITY
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ScriptScanRequest
from ._models import ToolSafetyPolicy
from ._python_rules import scan_python
from ._sanitizer import SafetySanitizer
from ._sanitizer import truncate_output
from ._common_rules import make_finding
from ._common_rules import RuleSpec
from ._models import RiskCategory

MAX_NESTED_PAYLOAD_DEPTH = 4
SCAN_ERROR_SPEC = RuleSpec(
    RiskCategory.POLICY,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Review the request because static scanning could not complete.",
)


class ToolScriptSafetyGuard:
    """Static scanner and decision aggregator."""

    def __init__(self, policy: ToolSafetyPolicy, sanitizer: SafetySanitizer | None = None):
        self.policy = policy
        self.sanitizer = sanitizer or SafetySanitizer()

    @classmethod
    def from_policy(cls, path: str) -> "ToolScriptSafetyGuard":
        """Create a guard from YAML."""
        return cls(ToolSafetyPolicy.from_yaml(path))

    def scan(self, request: ScriptScanRequest) -> SafetyReport:
        """Scan a normalized request."""
        started = time.perf_counter()
        findings, redacted = scan_limits(request, self.policy, self.sanitizer)
        context_findings, changed = self._scan_request_context(request)
        findings.extend(context_findings)
        redacted = redacted or changed
        if request.applicable and not request.payloads:
            findings.extend(self._missing_payload())
        for payload in request.payloads:
            payload_findings, changed = self._scan_payload(payload, request, 0)
            findings.extend(payload_findings)
            redacted = redacted or changed
        findings = self._deduplicate(findings)
        decision = self._decision(findings)
        risk = self._risk(findings)
        duration_ms = (time.perf_counter() - started) * 1000
        summary = self._summary(request, decision, findings)
        return SafetyReport(
            decision=decision,
            risk_level=risk,
            findings=findings,
            duration_ms=duration_ms,
            redacted=redacted,
            summary=summary,
            applicable=request.applicable,
            effective_timeout_seconds=request.effective_timeout_seconds,
            max_output_bytes=request.max_output_bytes,
        )

    def error_report(self, error: Exception) -> SafetyReport:
        """Convert scan/adapter failures into a sanitized blocking report."""
        finding, redacted = make_finding("POLICY005", error, SCAN_ERROR_SPEC, self.sanitizer)
        return SafetyReport(
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            findings=[finding],
            duration_ms=0,
            redacted=redacted,
            summary="needs_human_review: safety scan failed.",
            max_output_bytes=self.policy.max_output_bytes,
            effective_timeout_seconds=float(self.policy.max_timeout_seconds),
        )

    def limit_output(self, output: Any) -> Any:
        """Limit tool output to the configured byte budget."""
        return truncate_output(output, self.policy.max_output_bytes)

    def _scan_payload(
        self,
        payload: ScriptPayload,
        request: ScriptScanRequest,
        depth: int,
    ) -> tuple[list[SafetyFinding], bool]:
        findings = []
        redacted = False
        if payload.language == ScriptLanguage.PYTHON:
            language_findings, changed = scan_python(payload.content, request, self.policy, self.sanitizer)
        else:
            findings, redacted = scan_paths(payload.content, request, self.policy, self.sanitizer)
            language_findings, changed = scan_bash(payload.content, self.policy, self.sanitizer, request)
        findings.extend(language_findings)
        redacted = redacted or changed
        if payload.language == ScriptLanguage.BASH:
            nested_findings, changed = self._scan_nested(payload, request, depth)
            findings.extend(nested_findings)
            redacted = redacted or changed
        if payload.stdin:
            stdin_findings, changed = self._scan_stdin(payload, request, depth)
            findings.extend(stdin_findings)
            redacted = redacted or changed
        return findings, redacted

    def _scan_stdin(
        self,
        payload: ScriptPayload,
        request: ScriptScanRequest,
        depth: int,
    ) -> tuple[list[SafetyFinding], bool]:
        language = stdin_language(payload.content)
        if language is None:
            return scan_paths(payload.stdin, request, self.policy, self.sanitizer)
        stdin_payload = ScriptPayload(
            language=language,
            content=payload.stdin,
            source=f"{payload.source}.stdin",
        )
        return self._scan_payload(stdin_payload, request, depth + 1)

    def _scan_request_context(
        self,
        request: ScriptScanRequest,
    ) -> tuple[list[SafetyFinding], bool]:
        findings = []
        redacted = False
        context_values = [
            request.cwd,
            *request.env_keys,
            *(arg for payload in request.payloads for arg in payload.argv),
        ]
        for value in context_values:
            context_findings, changed = scan_paths(value, request, self.policy, self.sanitizer)
            findings.extend(context_findings)
            redacted = redacted or changed
        if request.background or request.tty:
            spec = RuleSpec(
                RiskCategory.PROCESS,
                RiskLevel.MEDIUM,
                SafetyDecision.NEEDS_HUMAN_REVIEW,
                "Disable background/TTY execution or obtain approval.",
            )
            finding, changed = make_finding("PROC003", "background or TTY execution requested", spec, self.sanitizer)
            findings.append(finding)
            redacted = redacted or changed
        return findings, redacted

    def _scan_nested(
        self,
        payload: ScriptPayload,
        request: ScriptScanRequest,
        depth: int,
    ) -> tuple[list[SafetyFinding], bool]:
        nested = nested_payloads(payload.content)
        if not nested:
            return [], False
        if depth >= MAX_NESTED_PAYLOAD_DEPTH:
            return self._recursion_finding(), False
        findings = []
        redacted = False
        for item in nested:
            item_findings, changed = self._scan_payload(item, request, depth + 1)
            findings.extend(item_findings)
            redacted = redacted or changed
        return findings, redacted

    def _missing_payload(self) -> list[SafetyFinding]:
        spec = RuleSpec(
            RiskCategory.POLICY,
            RiskLevel.MEDIUM,
            SafetyDecision.NEEDS_HUMAN_REVIEW,
            "Provide the executable payload for scanning.",
        )
        finding, _ = make_finding("POLICY003", "execution payload unavailable", spec, self.sanitizer)
        return [finding]

    def _recursion_finding(self) -> list[SafetyFinding]:
        spec = RuleSpec(
            RiskCategory.POLICY,
            RiskLevel.MEDIUM,
            SafetyDecision.NEEDS_HUMAN_REVIEW,
            "Review deeply nested interpreter commands.",
        )
        finding, _ = make_finding("POLICY004", "nested interpreter depth exceeded", spec, self.sanitizer)
        return [finding]

    @staticmethod
    def _deduplicate(findings: list[SafetyFinding]) -> list[SafetyFinding]:
        result = []
        seen = set()
        for finding in findings:
            key = (finding.rule_id, finding.evidence)
            if key not in seen:
                result.append(finding)
                seen.add(key)
        return sorted(result, key=lambda item: (item.rule_id, item.evidence))

    @staticmethod
    def _decision(findings: list[SafetyFinding]) -> SafetyDecision:
        return max(
            (finding.decision for finding in findings),
            key=lambda value: DECISION_PRIORITY[value],
            default=SafetyDecision.ALLOW,
        )

    @staticmethod
    def _risk(findings: list[SafetyFinding]) -> RiskLevel:
        return max(
            (finding.risk_level for finding in findings),
            key=lambda value: RISK_PRIORITY[value],
            default=RiskLevel.NONE,
        )

    @staticmethod
    def _summary(
        request: ScriptScanRequest,
        decision: SafetyDecision,
        findings: list[SafetyFinding],
    ) -> str:
        if not request.applicable:
            return "Tool is not an executable script entry point."
        if not findings:
            return "No configured static safety rule matched."
        return f"{decision.value}: {len(findings)} safety finding(s)."
