# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SafetyScanner — unified scan orchestrator for tool/script safety checking."""

from __future__ import annotations

import re
import time
from typing import Dict
from typing import List

from ._bash_parser import BashParser
from ._policy import PolicyConfig
from ._python_parser import PythonParser
from ._rules import SENSITIVE_ENV_KEYS
from ._rules import SENSITIVE_PATH_PATTERNS
from ._rules import SENSITIVE_WORD_PATTERNS
from ._rules import sanitize_text
from ._types import Decision
from ._types import RiskLevel
from ._types import RiskType
from ._types import SafetyFinding
from ._types import SafetyReport
from ._types import ScanRequest
from ._types import ScriptLanguage
from ._types import aggregate_decision
from ._types import max_risk_level


class SafetyScanner:
    """Unified safety scanner for tool/script execution.

    Orchestrates env check -> script parsing -> context check ->
    deduplication -> aggregation -> report generation.
    """

    def __init__(self, policy: PolicyConfig) -> None:
        self._policy = policy
        self._python_parser = PythonParser(policy)
        self._bash_parser = BashParser(policy)

    def scan(self, request: ScanRequest) -> SafetyReport:
        """Run a full safety scan and return a SafetyReport.

        Args:
            request: The scan request containing script and execution context.

        Returns:
            A fully populated SafetyReport.
        """
        start = time.monotonic()

        # Check for sensitive info to determine sanitized flag
        env_has_sensitive = self._is_env_contains_sensitive_keys(request.env)
        script_has_sensitive = sanitize_text(request.script, self._policy.secret_patterns) != request.script
        sanitized = env_has_sensitive or script_has_sensitive

        # Parse script content
        if request.language == ScriptLanguage.PYTHON:
            findings = self._python_parser.parse(request.script)
        else:
            findings = self._bash_parser.parse(request.script)

        # Context safety checks
        findings.extend(self._scan_context_safety(request))

        # Deduplicate
        findings = self._deduplicate_findings(findings)

        # Aggregate decision
        risk_level = max_risk_level(findings)
        decision = aggregate_decision(findings)
        rule_ids = sorted({f.rule_id for f in findings})

        # Build report
        duration_ms = int((time.monotonic() - start) * 1000)
        summary = self._generate_summary(decision, risk_level, rule_ids)
        blocked = decision == Decision.DENY

        telemetry_attributes = {
            "tool.safety.decision": decision.value,
            "tool.safety.risk_level": risk_level.value,
            "tool.safety.rule_id": ",".join(rule_ids) if rule_ids else "",
            "tool.safety.target": request.target.value,
            "tool.safety.language": request.language.value,
        }

        return SafetyReport(
            tool_name=request.tool_name,
            decision=decision,
            risk_level=risk_level,
            blocked=blocked,
            sanitized=sanitized,
            duration_ms=duration_ms,
            language=request.language,
            target=request.target,
            rule_ids=rule_ids,
            summary=summary,
            findings=findings,
            telemetry_attributes=telemetry_attributes,
        )

    def _is_env_contains_sensitive_keys(self, env: Dict[str, str]) -> bool:
        """Check if any environment variable key matches sensitive patterns.

        Keys listed in policy.env_allowlist are excluded from the check.
        """
        for key in env:
            if key in self._policy.env_allowlist:
                continue
            if SENSITIVE_ENV_KEYS.search(key):
                return True
        return False

    def _scan_context_safety(self, request: ScanRequest) -> List[SafetyFinding]:
        """Check execution context (args, cwd, metadata) for safety issues."""
        findings: List[SafetyFinding] = []

        # Check args for dangerous patterns
        for arg in request.args:
            if any(sensitive in arg for sensitive in SENSITIVE_PATH_PATTERNS):
                findings.append(
                    SafetyFinding(
                        rule_id="R001_CREDENTIAL_FILE_ACCESS",
                        rule_name="Sensitive Arg Path",
                        risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text(arg, self._policy.secret_patterns),
                        recommendation="Argument contains sensitive path. Review before execution.",
                    ))
            elif any(re.search(r'\b' + re.escape(word) + r'\b', arg) for word in SENSITIVE_WORD_PATTERNS):
                findings.append(
                    SafetyFinding(
                        rule_id="R001_CREDENTIAL_FILE_ACCESS",
                        rule_name="Sensitive Arg Path",
                        risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text(arg, self._policy.secret_patterns),
                        recommendation="Argument contains sensitive word. Review before execution.",
                    ))

        # Check cwd against denied paths
        if request.cwd and self._policy.is_path_denied(request.cwd):
            findings.append(
                SafetyFinding(
                    rule_id="R001_SYSTEM_PATH_OVERWRITE",
                    rule_name="Denied Working Directory",
                    risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel.CRITICAL,
                    evidence=sanitize_text(request.cwd, self._policy.secret_patterns),
                    recommendation=f"Working directory '{request.cwd}' is denied by safety policy.",
                ))

        # Check tool metadata limits
        metadata = request.tool_metadata
        timeout = metadata.get("timeout", 0)
        if isinstance(timeout, (int, float)) and timeout > self._policy.max_timeout_seconds:
            findings.append(
                SafetyFinding(
                    rule_id="R005_RESOURCE_ABUSE",
                    rule_name="Timeout Exceeded",
                    risk_type=RiskType.RESOURCE_ABUSE,
                    risk_level=RiskLevel.HIGH,
                    evidence=f"timeout={timeout}",
                    recommendation=f"Timeout {timeout}s exceeds max allowed {self._policy.max_timeout_seconds}s.",
                ))

        max_output = metadata.get("max_output_bytes", 0)
        if isinstance(max_output, (int, float)) and max_output > self._policy.max_output_bytes:
            findings.append(
                SafetyFinding(
                    rule_id="R005_RESOURCE_ABUSE",
                    rule_name="Output Limit Exceeded",
                    risk_type=RiskType.RESOURCE_ABUSE,
                    risk_level=RiskLevel.HIGH,
                    evidence=f"max_output_bytes={max_output}",
                    recommendation=(f"Requested output limit {max_output} bytes exceeds "
                                    f"max allowed {self._policy.max_output_bytes} bytes."),
                ))

        return findings

    @staticmethod
    def _deduplicate_findings(findings: List[SafetyFinding]) -> List[SafetyFinding]:
        """Deduplicate findings by (rule_id, line) key."""
        seen: set[tuple] = set()
        result: List[SafetyFinding] = []
        for f in findings:
            key = (f.rule_id, f.line)
            if key not in seen:
                seen.add(key)
                result.append(f)
        return result

    @staticmethod
    def _generate_summary(decision: Decision, risk_level: RiskLevel, rule_ids: List[str]) -> str:
        """Generate a human-readable summary of the scan result."""
        rule_count = len(rule_ids)
        rule_list = ", ".join(rule_ids[:5])
        if len(rule_ids) > 5:
            rule_list += f", ... ({rule_count} total)"

        if decision == Decision.ALLOW:
            return f"Safety scan passed. Risk level: {risk_level.value}."
        elif decision == Decision.DENY:
            return f"Execution blocked. Risk level: {risk_level.value}. Rules triggered: {rule_list}"
        else:
            return f"Human review required. Risk level: {risk_level.value}. Rules triggered: {rule_list}"
