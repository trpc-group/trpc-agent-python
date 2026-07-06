# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool script safety scanner."""

from __future__ import annotations

import time
import shlex
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path

from ._policy import ToolSafetyPolicy
from ._rules import _finding
from ._rules import SENSITIVE_NAME_RE
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
    """Static pre-execution scanner for Python scripts and Bash commands."""

    def __init__(self, policy: ToolSafetyPolicy | None = None):
        self.policy = policy or ToolSafetyPolicy.default()

    def scan(self, request: ToolScriptScanRequest) -> SafetyReport:
        started = time.perf_counter()
        language = self._normalize_language(request.language)
        sanitized = self._env_contains_sensitive_keys(request.env)
        _, script_sanitized = sanitize_text(request.script, limit=max(len(request.script), 1))
        sanitized = sanitized or script_sanitized

        if language == "python":
            findings = scan_python_script(request.script, self.policy)
        elif language in {"bash", "sh", "shell"}:
            findings = scan_bash_script(request.script, self.policy)
        else:
            findings = scan_bash_script(request.script, self.policy)
            findings.extend(scan_python_script(request.script, self.policy))
        findings.extend(self._scan_execution_context(request))

        decision = aggregate_decision(findings)
        risk_level = max_risk_level(findings)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        blocked = decision == Decision.DENY
        rule_ids = [finding.rule_id for finding in findings]
        summary = self._build_summary(decision.value, risk_level.value, rule_ids)
        scan_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        telemetry_attributes = {
            "tool.safety.scan_id": scan_id,
            "tool.safety.decision": decision.value,
            "tool.safety.risk_level": risk_level.value,
            "tool.safety.rule_id": ",".join(rule_ids[:10]),
            "tool.safety.blocked": blocked,
            "tool.safety.sanitized": sanitized,
            "tool.safety.tool_name": request.tool_name,
            "tool.safety.duration_ms": elapsed_ms,
        }
        return SafetyReport(
            scan_id=scan_id,
            timestamp=timestamp,
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            tool_name=request.tool_name,
            language=language,
            elapsed_ms=elapsed_ms,
            sanitized=sanitized,
            blocked=blocked,
            summary=summary,
            telemetry_attributes=telemetry_attributes,
        )

    def scan_script(
        self,
        script: str,
        language: str,
        *,
        command_args: list[str] | None = None,
        cwd: str = "",
        env: dict[str, str] | None = None,
        tool_name: str = "unknown_tool",
        tool_metadata: dict | None = None,
    ) -> SafetyReport:
        return self.scan(
            ToolScriptScanRequest(
                script=script,
                language=language,
                command_args=command_args or [],
                cwd=cwd,
                env=env or {},
                tool_name=tool_name,
                tool_metadata=tool_metadata or {},
            ))

    def scan_file(
        self,
        path: str | Path,
        *,
        language: str | None = None,
        command_args: list[str] | None = None,
        cwd: str = "",
        env: dict[str, str] | None = None,
        tool_name: str = "unknown_tool",
        tool_metadata: dict | None = None,
    ) -> SafetyReport:
        file_path = Path(path)
        script = file_path.read_text(encoding="utf-8")
        return self.scan_script(
            script,
            language or self.infer_language(file_path),
            command_args=command_args,
            cwd=cwd,
            env=env,
            tool_name=tool_name,
            tool_metadata=tool_metadata,
        )

    @staticmethod
    def infer_language(path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            return "python"
        if suffix in {".sh", ".bash"}:
            return "bash"
        return "unknown"

    @staticmethod
    def _normalize_language(language: str) -> str:
        normalized = (language or "unknown").lower()
        if normalized in {"py", "python3"}:
            return "python"
        if normalized in {"shell", "sh"}:
            return "bash"
        return normalized

    @staticmethod
    def _env_contains_sensitive_keys(env: dict[str, str]) -> bool:
        return any(SENSITIVE_NAME_RE.search(key or "") for key in env)

    @staticmethod
    def _build_summary(decision: str, risk_level: str, rule_ids: list[str]) -> str:
        if not rule_ids:
            return "No safety rules matched; execution is allowed by the current static policy."
        return f"Decision {decision} with {risk_level} risk from rules: {', '.join(rule_ids[:5])}."

    def _scan_execution_context(self, request: ToolScriptScanRequest) -> list[RiskFinding]:
        findings: list[RiskFinding] = []
        if request.command_args:
            command_text = shlex.join(request.command_args)
            findings.extend(scan_bash_script(command_text, self.policy))

        if request.cwd and self.policy.is_path_denied(request.cwd):
            findings.append(
                _finding(
                    "EXECUTION_DENIED_CWD",
                    "dangerous_file_operation",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    request.cwd,
                    "Do not execute tools with a working directory inside denied credential or system paths.",
                    f"Execution cwd is denied by policy: {request.cwd}.",
                    metadata={"cwd": request.cwd},
                ))

        timeout = self._metadata_number(request.tool_metadata, ("timeout", "timeout_seconds", "max_timeout_seconds"))
        if timeout is not None and timeout > self.policy.max_timeout_seconds:
            findings.append(
                _finding(
                    "RESOURCE_TIMEOUT_LIMIT_EXCEEDED",
                    "resource_abuse",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    str(timeout),
                    "Lower the requested timeout or update max_timeout_seconds after review.",
                    f"Requested timeout {timeout} exceeds policy limit {self.policy.max_timeout_seconds}.",
                    metadata={
                        "timeout": timeout,
                        "max_timeout_seconds": self.policy.max_timeout_seconds
                    },
                ))

        output_size = self._metadata_number(
            request.tool_metadata,
            ("max_output_bytes", "output_bytes", "output_size", "max_output_size"),
        )
        if output_size is not None and output_size > self.policy.max_output_bytes:
            findings.append(
                _finding(
                    "RESOURCE_OUTPUT_LIMIT_EXCEEDED",
                    "resource_abuse",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    str(output_size),
                    "Lower the requested output size or update max_output_bytes after review.",
                    f"Requested output size {output_size} exceeds policy limit {self.policy.max_output_bytes}.",
                    metadata={
                        "output_size": output_size,
                        "max_output_bytes": self.policy.max_output_bytes
                    },
                ))
        return findings

    @staticmethod
    def _metadata_number(metadata: dict, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            if key not in metadata:
                continue
            try:
                return float(metadata[key])
            except (TypeError, ValueError):
                return None
        return None
