# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report-producing tool script safety scanner."""

from __future__ import annotations

import shlex
import time
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from ._policy import ToolSafetyPolicy
from ._rules import scan_bash_script
from ._rules import scan_python_script
from ._rules import sanitize_text
from ._types import Decision
from ._types import RiskFinding
from ._types import RiskLevel
from ._types import SafetyReport
from ._types import ToolScriptScanRequest
from ._types import aggregate_decision
from ._types import max_risk_level


class ToolScriptSafetyScanner:
    """Static scanner for tool scripts and shell command arguments."""

    def __init__(self, policy: ToolSafetyPolicy | None = None) -> None:
        self.policy = policy or ToolSafetyPolicy.default()

    def scan(self, request: ToolScriptScanRequest) -> SafetyReport:
        """Scan a script request and return a structured report."""
        started = time.perf_counter()
        language = self.normalize_language(request.language)
        findings: list[RiskFinding] = []

        if language == "python":
            findings.extend(scan_python_script(request.script, self.policy))
        elif language == "bash":
            findings.extend(scan_bash_script(request.script, self.policy))
        else:
            findings.extend(scan_python_script(request.script, self.policy))
            findings.extend(scan_bash_script(request.script, self.policy))

        if request.command_args:
            findings.extend(scan_bash_script(shlex.join(request.command_args), self.policy))

        if request.cwd and self.policy.is_path_denied(request.cwd):
            findings.append(
                self._finding(
                    "TOOL_CWD_DENIED_PATH",
                    "denied_path",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    request.cwd,
                    "Use a working directory outside denied credential or system paths.",
                    "Tool working directory matches a denied path.",
                )
            )

        findings.extend(self._scan_tool_metadata(request.tool_metadata))
        findings = self._dedupe_findings(findings)

        decision = aggregate_decision(findings)
        risk_level = max_risk_level(findings)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        sanitized = any(finding.metadata.get("sanitized") for finding in findings)
        blocked = self.policy.should_block(decision)
        scan_id = str(uuid.uuid4())
        telemetry_attributes = self._telemetry_attributes(
            scan_id=scan_id,
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            blocked=blocked,
            sanitized=sanitized,
            tool_name=request.tool_name,
            elapsed_ms=elapsed_ms,
        )
        report = SafetyReport(
            scan_id=scan_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            tool_name=request.tool_name,
            language=language,
            elapsed_ms=elapsed_ms,
            sanitized=sanitized,
            blocked=blocked,
            summary=self._summary(decision, risk_level, findings, blocked),
            telemetry_attributes=telemetry_attributes,
        )
        return report

    def scan_script(
        self,
        script: str,
        language: str,
        *,
        command_args: list[str] | None = None,
        cwd: str = "",
        env: dict[str, str] | None = None,
        tool_name: str = "unknown_tool",
        tool_metadata: dict[str, Any] | None = None,
    ) -> SafetyReport:
        """Convenience wrapper around scan()."""
        return self.scan(
            ToolScriptScanRequest(
                script=script,
                language=language,
                command_args=command_args or [],
                cwd=cwd,
                env=env or {},
                tool_name=tool_name,
                tool_metadata=tool_metadata or {},
            )
        )

    def scan_file(
        self,
        path: str,
        *,
        language: str | None = None,
        command_args: list[str] | None = None,
        cwd: str = "",
        env: dict[str, str] | None = None,
        tool_name: str = "unknown_tool",
        tool_metadata: dict[str, Any] | None = None,
    ) -> SafetyReport:
        """Read and scan a script file."""
        file_path = Path(path)
        return self.scan_script(
            file_path.read_text(encoding="utf-8"),
            language or self.infer_language(str(file_path)),
            command_args=command_args,
            cwd=cwd,
            env=env,
            tool_name=tool_name,
            tool_metadata=tool_metadata,
        )

    @staticmethod
    def infer_language(path: str) -> str:
        """Infer scanner language from a file extension."""
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in {".sh", ".bash", ".zsh", ".ksh"}:
            return "bash"
        return "unknown"

    @staticmethod
    def normalize_language(language: str) -> str:
        """Normalize user-provided language names."""
        normalized = (language or "unknown").strip().lower()
        if normalized in {"py", "python3"}:
            return "python"
        if normalized in {"sh", "shell", "zsh", "ksh"}:
            return "bash"
        if normalized in {"python", "bash"}:
            return normalized
        return "unknown"

    def _scan_tool_metadata(self, metadata: dict[str, Any]) -> list[RiskFinding]:
        findings: list[RiskFinding] = []
        timeout = metadata.get("timeout")
        if timeout is not None:
            try:
                if float(timeout) > self.policy.max_timeout_seconds:
                    findings.append(
                        self._finding(
                            "TOOL_TIMEOUT_REVIEW",
                            "resource_limit",
                            RiskLevel.MEDIUM,
                            Decision.NEEDS_HUMAN_REVIEW,
                            f"timeout={timeout}",
                            "Use a timeout at or below max_timeout_seconds or review the exception.",
                            "Tool timeout exceeds policy threshold.",
                        )
                    )
            except (TypeError, ValueError):
                findings.append(
                    self._finding(
                        "TOOL_TIMEOUT_DYNAMIC_REVIEW",
                        "resource_limit",
                        RiskLevel.LOW,
                        Decision.NEEDS_HUMAN_REVIEW,
                        "timeout=<dynamic>",
                        "Use a numeric timeout before executing the tool.",
                        "Tool timeout is dynamic or invalid.",
                    )
                )

        max_output_bytes = metadata.get("max_output_bytes")
        if max_output_bytes is not None:
            try:
                if int(max_output_bytes) > self.policy.max_output_bytes:
                    findings.append(
                        self._finding(
                            "TOOL_OUTPUT_LIMIT_REVIEW",
                            "resource_limit",
                            RiskLevel.MEDIUM,
                            Decision.NEEDS_HUMAN_REVIEW,
                            f"max_output_bytes={max_output_bytes}",
                            "Use a bounded output size at or below max_output_bytes or review the exception.",
                            "Tool output byte limit exceeds policy threshold.",
                        )
                    )
            except (TypeError, ValueError):
                findings.append(
                    self._finding(
                        "TOOL_OUTPUT_LIMIT_DYNAMIC_REVIEW",
                        "resource_limit",
                        RiskLevel.LOW,
                        Decision.NEEDS_HUMAN_REVIEW,
                        "max_output_bytes=<dynamic>",
                        "Use a numeric output byte limit before executing the tool.",
                        "Tool output byte limit is dynamic or invalid.",
                    )
                )
        return findings

    def _finding(
        self,
        rule_id: str,
        risk_type: str,
        risk_level: RiskLevel,
        decision: Decision,
        evidence: str,
        recommendation: str,
        message: str,
    ) -> RiskFinding:
        evidence_text, sanitized = sanitize_text(evidence)
        return RiskFinding(
            rule_id=rule_id,
            risk_type=risk_type,
            risk_level=risk_level,
            decision=decision,
            evidence=evidence_text,
            recommendation=recommendation,
            message=message,
            metadata={"sanitized": sanitized} if sanitized else {},
        )

    @staticmethod
    def _dedupe_findings(findings: list[RiskFinding]) -> list[RiskFinding]:
        seen: set[tuple[str, int | None, str]] = set()
        deduped: list[RiskFinding] = []
        for finding in findings:
            key = (finding.rule_id, finding.line, finding.evidence)
            if key not in seen:
                seen.add(key)
                deduped.append(finding)
        return deduped

    @staticmethod
    def _summary(decision: Decision, risk_level: RiskLevel, findings: list[RiskFinding], blocked: bool) -> str:
        action = "blocked" if blocked else "not blocked"
        if decision == Decision.ALLOW:
            return "Safety scan allowed execution with no findings."
        return (
            f"Safety scan returned {decision.value} ({risk_level.value}) with "
            f"{len(findings)} finding(s); execution is {action}."
        )

    @staticmethod
    def _telemetry_attributes(
        *,
        scan_id: str,
        decision: Decision,
        risk_level: RiskLevel,
        findings: list[RiskFinding],
        blocked: bool,
        sanitized: bool,
        tool_name: str,
        elapsed_ms: float,
    ) -> dict[str, Any]:
        return {
            "tool.safety.scan_id": scan_id,
            "tool.safety.decision": decision.value,
            "tool.safety.risk_level": risk_level.value,
            "tool.safety.rule_id": ",".join(finding.rule_id for finding in findings),
            "tool.safety.blocked": blocked,
            "tool.safety.sanitized": sanitized,
            "tool.safety.tool_name": tool_name,
            "tool.safety.duration_ms": elapsed_ms,
        }
