# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""JSONL audit logging for tool safety checks."""

from __future__ import annotations

import json
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Optional

from .models import Finding
from .models import SafetyDecision
from .models import SafetyResult
from .models import SafetySeverity
from .models import ToolExecutionRequest

DEFAULT_AUDIT_LOG_FILE = Path("tool_safety_audit.jsonl")
_SENSITIVE_KEYS = {"api_key", "authorization", "cookie", "password", "secret", "token"}
_SCRIPT_KEYS = {"bash_code", "cmd", "code", "command", "python_code", "script"}
_SEVERITY_RANK = {
    SafetySeverity.INFO: 0,
    SafetySeverity.LOW: 1,
    SafetySeverity.MEDIUM: 2,
    SafetySeverity.HIGH: 3,
    SafetySeverity.CRITICAL: 4,
}


class SafetyAuditLogger:
    """Append tool safety audit events as JSON Lines."""

    def __init__(self, path: str | Path = DEFAULT_AUDIT_LOG_FILE):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Return the audit log path."""
        return self._path

    def write(self, result: SafetyResult, latency_ms: float) -> None:
        """Write one audit record."""
        record = build_audit_record(result, latency_ms)
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            fp.write("\n")


def build_audit_record(result: SafetyResult, latency_ms: float) -> dict[str, Any]:
    """Build one ELK/Grafana-friendly audit record."""
    request = result.request or ToolExecutionRequest()
    current_risk_level = risk_level(result.findings)
    rule_ids = [finding.rule_id for finding in result.findings]
    return {
        "timestamp": _utc_now(),
        "tool_name": request.tool_name,
        "decision": result.decision.value,
        "risk_level": current_risk_level.value,
        "rule_id": ",".join(rule_ids),
        "rule_ids": rule_ids,
        "latency": round(latency_ms, 3),
        "latency_ms": round(latency_ms, 3),
        "blocked": result.decision != SafetyDecision.ALLOW,
        "desensitized": True,
        "agent_name": request.agent_name,
        "invocation_id": request.invocation_id,
        "function_call_id": request.function_call_id,
        "language": request.language,
        "finding_count": len(result.findings),
        "findings": [_finding_record(finding) for finding in result.findings],
        "request": _request_record(request),
    }


def risk_level(findings: list[Finding]) -> SafetySeverity:
    """Return the highest severity represented by a list of findings."""
    if not findings:
        return SafetySeverity.LOW
    return max((finding.severity for finding in findings), key=lambda severity: _SEVERITY_RANK[severity])


def _finding_record(finding: Finding) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity.value,
        "target": _desensitize_value(finding.target),
        "message": finding.message,
        "metadata": _desensitize_value(finding.metadata),
    }


def _request_record(request: ToolExecutionRequest) -> dict[str, Any]:
    return {
        "args": _desensitize_args(request.args),
        "metadata": _desensitize_value(request.metadata),
        "script_present": bool(request.script),
        "script_length": len(request.script or ""),
    }


def _desensitize_args(args: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _desensitize_arg_value(str(key), value) for key, value in args.items()}


def _desensitize_arg_value(key: str, value: Any) -> Any:
    lowered_key = key.lower()
    if lowered_key in _SCRIPT_KEYS:
        return _desensitize_script_value(value)
    if _is_sensitive_key(lowered_key):
        return "***"
    return _desensitize_value(value)


def _desensitize_script_value(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else ""
    return {
        "redacted": True,
        "length": len(text),
    }


def _desensitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "***" if _is_sensitive_key(str(key)) else _desensitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_desensitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_desensitize_value(item) for item in value]
    if isinstance(value, str):
        return _desensitize_string(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(sensitive_key in lowered for sensitive_key in _SENSITIVE_KEYS)


def _desensitize_string(value: str) -> str:
    if len(value) <= 256:
        return value
    return f"{value[:128]}...<truncated:{len(value)}>"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def monotonic_ms(start: Optional[float] = None) -> float:
    """Return monotonic milliseconds since start, or current monotonic milliseconds."""
    now = time.monotonic() * 1000
    if start is None:
        return now
    return now - start
