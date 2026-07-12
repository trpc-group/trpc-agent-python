# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Static safety scanner for Python scripts and shell commands."""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import ToolSafetyReport
from ._models import ToolSafetyRequest
from ._policy import ToolSafetyPolicy

_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|passwd|private[_-]?key|secret)"
)
_URL_RE = re.compile(r"https?://[^\s'\"\\)]+", re.IGNORECASE)


class ToolScriptSafetyScanner:
    """Apply deterministic rules to script execution inputs."""

    def __init__(self, policy: ToolSafetyPolicy | None = None):
        self.policy = policy or ToolSafetyPolicy()

    @classmethod
    def from_policy_file(cls, path: str | Path) -> "ToolScriptSafetyScanner":
        return cls(ToolSafetyPolicy.from_yaml(path))

    def scan(self, request: ToolSafetyRequest) -> ToolSafetyReport:
        """Scan a request without executing or importing its script."""
        started = time.perf_counter()
        text = " ".join([request.script, *request.command_args])
        findings: list[SafetyFinding] = []

        self._scan_paths(text, request.working_directory, findings)
        self._scan_network(text, findings)
        self._scan_processes(text, request.language, findings)
        self._scan_dependencies(text, findings)
        self._scan_resources(text, request.metadata, findings)
        redacted = self._scan_secrets(text, request.environment, findings)

        risk_level = max(
            (finding.risk_level for finding in findings),
            key=lambda level: _RISK_ORDER[level],
            default=RiskLevel.NONE,
        )
        levels = {finding.risk_level.value for finding in findings}
        if levels.intersection(self.policy.deny_risk_levels):
            decision = SafetyDecision.DENY
        elif levels.intersection(self.policy.review_risk_levels):
            decision = SafetyDecision.NEEDS_HUMAN_REVIEW
        else:
            decision = SafetyDecision.ALLOW
        rule_ids = list(dict.fromkeys(finding.rule_id for finding in findings))
        duration_ms = (time.perf_counter() - started) * 1000
        attributes = {
            "tool.safety.decision": decision.value,
            "tool.safety.risk_level": risk_level.value,
            "tool.safety.rule_id": ",".join(rule_ids),
        }
        return ToolSafetyReport(
            tool_name=request.tool_name,
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            rule_ids=rule_ids,
            duration_ms=duration_ms,
            redacted=redacted,
            blocked=decision != SafetyDecision.ALLOW,
            telemetry_attributes=attributes,
        )

    @staticmethod
    def _add(
        findings: list[SafetyFinding],
        risk_type: str,
        level: RiskLevel,
        rule_id: str,
        evidence: str,
        recommendation: str,
        text: str,
    ) -> None:
        evidence = evidence[:160]
        line = text[: text.find(evidence)].count("\n") + 1 if evidence in text else None
        findings.append(
            SafetyFinding(
                risk_type=risk_type,
                risk_level=level,
                rule_id=rule_id,
                evidence=evidence,
                recommendation=recommendation,
                line=line,
            )
        )

    def _scan_paths(
        self, text: str, working_directory: str | None, findings: list[SafetyFinding]
    ) -> None:
        destructive = re.search(
            r"(?im)(rm\s+-[^\n]*r[^\n]*\s+(?:/|~)|shutil\.rmtree\s*\(|os\.remove\s*\(|"
            r"(?:open|Path)\s*\([^\n]+['\"]w['\"])",
            text,
        )
        if destructive:
            self._add(findings, "dangerous_file_operation", RiskLevel.CRITICAL,
                      "FILE_DESTRUCTIVE", destructive.group(0),
                      "Restrict writes to an isolated workspace and require explicit approval.", text)
        haystack = " ".join(filter(None, [text, working_directory]))
        for forbidden in self.policy.forbidden_paths:
            expanded = str(Path(forbidden).expanduser())
            candidates = {forbidden, expanded, forbidden.replace("~", "$HOME")}
            matched = next((candidate for candidate in candidates if candidate and candidate in haystack), None)
            if matched:
                self._add(findings, "sensitive_path_access", RiskLevel.HIGH,
                          "PATH_FORBIDDEN", matched,
                          "Remove sensitive paths or use a narrowly scoped secret provider.", haystack)

    def _scan_network(self, text: str, findings: list[SafetyFinding]) -> None:
        network_api = re.search(r"(?i)\b(curl|wget|requests\.|aiohttp\.|socket\.)", text)
        urls = _URL_RE.findall(text)
        for url in urls:
            host = (urlparse(url).hostname or "").lower()
            allowed = any(host == domain.lower() or host.endswith("." + domain.lower())
                          for domain in self.policy.allowed_domains)
            if not allowed:
                self._add(findings, "network_egress", RiskLevel.HIGH,
                          "NET_DOMAIN_NOT_ALLOWED", url,
                          "Add a reviewed domain to allowed_domains or remove the request.", text)
        if network_api and not urls:
            self._add(findings, "network_egress", RiskLevel.MEDIUM,
                      "NET_DYNAMIC_DESTINATION", network_api.group(0),
                      "Resolve the destination and request human review.", text)

    def _scan_processes(
        self, text: str, language: str, findings: list[SafetyFinding]
    ) -> None:
        shell_injection = re.search(r"(?m)(;|&&|\|\||`[^`]+`|\$\([^\)]+\))", text)
        if shell_injection:
            self._add(findings, "shell_injection", RiskLevel.HIGH,
                      "SHELL_CONTROL_OPERATOR", shell_injection.group(0),
                          "Use an argv list and avoid shell interpolation/control operators.", text)
        pipeline = re.search(r"(?<!\|)\|(?!\|)", text) if language in ("auto", "bash", "shell") else None
        if pipeline:
            self._add(findings, "shell_pipeline", RiskLevel.MEDIUM,
                      "SHELL_PIPELINE_REVIEW", pipeline.group(0),
                      "Review every pipeline stage and avoid passing untrusted data.", text)
        process = re.search(r"(?i)\b(subprocess\.|os\.system\s*\(|sudo\b|nohup\b|&\s*$)", text)
        if process:
            self._add(findings, "process_execution", RiskLevel.MEDIUM,
                      "PROCESS_SPAWN", process.group(0),
                      "Use an allowed command with bounded arguments or request review.", text)
        command = self._first_command(text) if language in ("auto", "bash", "shell") else None
        if command and command not in self.policy.allowed_commands and not process:
            if re.match(r"^[A-Za-z0-9_.-]+(?:\s|$)", text.strip()):
                self._add(findings, "command_policy", RiskLevel.MEDIUM,
                          "COMMAND_NOT_ALLOWED", command,
                          "Add the reviewed command to allowed_commands.", text)

    def _scan_dependencies(self, text: str, findings: list[SafetyFinding]) -> None:
        match = re.search(r"(?i)\b(?:pip(?:3)?|npm|yarn|apt(?:-get)?|brew)\s+install\b", text)
        if match:
            self._add(findings, "dependency_install", RiskLevel.HIGH,
                      "DEPENDENCY_INSTALL", match.group(0),
                      "Build dependencies into a reviewed immutable environment.", text)

    def _scan_resources(
        self, text: str, metadata: dict, findings: list[SafetyFinding]
    ) -> None:
        patterns: Iterable[tuple[str, RiskLevel, str]] = (
            (r"(?m)while\s+True\s*:", RiskLevel.HIGH, "RESOURCE_INFINITE_LOOP"),
            (r":\(\)\s*\{\s*:\|:&\s*;\s*\}", RiskLevel.CRITICAL, "RESOURCE_FORK_BOMB"),
            (r"(?i)(?:time\.)?sleep\s*\(\s*(?:[6-9]\d|\d{3,})", RiskLevel.MEDIUM, "RESOURCE_LONG_SLEEP"),
            (r"(?i)(?:ThreadPoolExecutor|ProcessPoolExecutor)\s*\(\s*(?:[5-9]\d|\d{3,})", RiskLevel.HIGH,
             "RESOURCE_CONCURRENCY"),
        )
        for pattern, level, rule_id in patterns:
            match = re.search(pattern, text)
            if match:
                self._add(findings, "resource_abuse", level, rule_id, match.group(0),
                          "Apply timeout, process, memory and concurrency limits in a sandbox.", text)
        timeout = metadata.get("timeout")
        if isinstance(timeout, (int, float)) and timeout > self.policy.max_timeout_seconds:
            self._add(findings, "resource_abuse", RiskLevel.MEDIUM,
                      "RESOURCE_TIMEOUT_LIMIT", str(timeout),
                      f"Limit timeout to {self.policy.max_timeout_seconds} seconds.", text)

    def _scan_secrets(
        self, text: str, environment: dict[str, str], findings: list[SafetyFinding]
    ) -> bool:
        output = re.search(
            r"(?is)(print|logging\.[a-z]+|echo|curl|requests\.(?:post|put))[^\n]{0,160}"
            r"(api[_-]?key|token|password|private[_-]?key|secret)",
            text,
        )
        sensitive_env = [name for name in environment if _SECRET_RE.search(name)]
        if output or sensitive_env:
            evidence = output.group(0) if output else f"environment keys: {', '.join(sensitive_env)}"
            self._add(findings, "sensitive_data_exposure", RiskLevel.HIGH,
                      "SECRET_EXPOSURE", evidence,
                      "Redact secrets and pass opaque references instead of secret values.", text)
            return True
        return False

    @staticmethod
    def _first_command(text: str) -> str | None:
        stripped = text.strip()
        if not stripped or "\n" in stripped or re.match(r"^(?:from|import|def|class)\b", stripped):
            return None
        try:
            tokens = shlex.split(stripped, posix=True)
        except ValueError:
            return None
        return tokens[0] if tokens else None
