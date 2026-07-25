# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Policy-driven static scanner for Python scripts and Bash commands."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Optional

from ._bash_scanner import scan_bash
from ._models import Decision
from ._models import RISK_LEVEL_ORDER
from ._models import RiskLevel
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._policy import ToolSafetyPolicy
from ._python_scanner import scan_python
from ._rule_utils import SECRET_NAME_RE
from ._rule_utils import add_finding
from ._rule_utils import path_is_denied
from ._rule_utils import redact

_SHELL_META_RE = re.compile(r"(?:;|&&|\|\||`|\$\()")


class ToolSafetyScanner:
    """Perform deterministic safety checks before script execution."""

    def __init__(self, policy: Optional[ToolSafetyPolicy] = None):
        self.policy = policy or ToolSafetyPolicy()

    def scan_command(
        self,
        command: str,
        *,
        tool_name: str = "Bash",
        command_args: Optional[list[str]] = None,
        working_directory: Optional[str] = None,
        environment: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> SafetyReport:
        """Scan one Bash command."""
        return self.scan(
            SafetyScanRequest(
                script=command,
                language="bash",
                command_args=command_args or [],
                working_directory=working_directory,
                environment=environment or {},
                tool_name=tool_name,
                timeout_seconds=timeout_seconds,
            ))

    def scan(self, request: SafetyScanRequest) -> SafetyReport:
        """Scan script content and execution context without executing them."""
        started = time.perf_counter()
        secrets = [value for key, value in request.environment.items() if value and SECRET_NAME_RE.search(key)]
        findings = self._scan_context(request, secrets)
        language = request.language.strip().lower()
        oversized = (len(request.script.encode("utf-8")) > self.policy.max_script_bytes
                     and "RESOURCE-005" not in self.policy.disabled_rules)
        if language in {"python", "py", "python3"}:
            normalized_language = "python"
            if not oversized:
                findings.extend(scan_python(request.script, self.policy, secrets))
        elif language in {"bash", "sh", "shell"}:
            normalized_language = "bash"
            if not oversized:
                findings.extend(scan_bash(request.script, self.policy, secrets))
        else:
            normalized_language = language or "unknown"
            add_finding(
                findings,
                self.policy,
                category="unsupported_language",
                rule_id="LANGUAGE-001",
                title="Script language is not supported by the scanner",
                risk_level=RiskLevel.MEDIUM,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                evidence=f"language={normalized_language}",
                recommendation="Add a scanner for this language or require a human security review.",
            )

        findings = self._deduplicate(findings)
        decision = self._decision(findings)
        risk_level = max(
            (finding.risk_level for finding in findings),
            key=lambda level: RISK_LEVEL_ORDER[level],
            default=RiskLevel.NONE,
        )
        return SafetyReport(
            decision=decision,
            risk_level=risk_level,
            rule_ids=list(dict.fromkeys(finding.rule_id for finding in findings)),
            findings=findings,
            tool_name=request.tool_name,
            language=normalized_language,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            script_sha256=hashlib.sha256(request.script.encode("utf-8")).hexdigest(),
            policy_version=self.policy.version,
            redacted=bool(secrets) or any(finding.redacted for finding in findings),
        )

    def _scan_context(self, request: SafetyScanRequest, secrets: list[str]) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        script_size = len(request.script.encode("utf-8"))
        if script_size > self.policy.max_script_bytes:
            add_finding(
                findings,
                self.policy,
                category="resource_abuse",
                rule_id="RESOURCE-005",
                title="Script exceeds the configured scan size",
                risk_level=RiskLevel.HIGH,
                decision=Decision.DENY,
                evidence=f"script size={script_size} bytes, maximum={self.policy.max_script_bytes} bytes",
                recommendation="Reduce the script size or raise the reviewed policy limit.",
            )
        if request.working_directory and path_is_denied(request.working_directory, self.policy):
            evidence, was_redacted = redact(
                request.working_directory,
                secrets,
                self.policy.max_evidence_chars,
            )
            add_finding(
                findings,
                self.policy,
                category="dangerous_file_operation",
                rule_id="FILE-002",
                title="Working directory is protected by policy",
                risk_level=RiskLevel.HIGH,
                decision=Decision.DENY,
                evidence=f"working_directory={evidence}",
                recommendation="Run in a dedicated workspace without credentials or system files.",
                redacted=was_redacted,
            )
        for index, argument in enumerate(request.command_args):
            if not _SHELL_META_RE.search(argument):
                continue
            evidence, was_redacted = redact(argument, secrets, self.policy.max_evidence_chars)
            add_finding(
                findings,
                self.policy,
                category="process_execution",
                rule_id="ARG-001",
                title="Command argument contains shell control syntax",
                risk_level=RiskLevel.HIGH,
                decision=Decision.DENY,
                evidence=f"argv[{index}]={evidence}",
                recommendation="Pass arguments to a non-shell API and validate each value.",
                redacted=was_redacted,
            )
        if request.timeout_seconds and request.timeout_seconds > self.policy.max_timeout_seconds:
            add_finding(
                findings,
                self.policy,
                category="policy_limit",
                rule_id="POLICY-001",
                title="Requested timeout exceeds policy",
                risk_level=RiskLevel.MEDIUM,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                evidence=f"timeout={request.timeout_seconds}s, maximum={self.policy.max_timeout_seconds}s",
                recommendation="Lower the timeout or obtain approval for a policy exception.",
            )
        return findings

    @staticmethod
    def _decision(findings: list[SafetyFinding]) -> Decision:
        if any(finding.decision == Decision.DENY for finding in findings):
            return Decision.DENY
        if any(finding.decision == Decision.NEEDS_HUMAN_REVIEW for finding in findings):
            return Decision.NEEDS_HUMAN_REVIEW
        return Decision.ALLOW

    @staticmethod
    def _deduplicate(findings: list[SafetyFinding]) -> list[SafetyFinding]:
        unique = []
        seen = set()
        for finding in findings:
            key = (finding.rule_id, finding.line_number, finding.evidence)
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
        return unique
