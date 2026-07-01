# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Stable JSON report generation for tool safety checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audit import risk_level
from .models import Finding
from .models import SafetyDecision
from .models import SafetyResult
from .models import ToolExecutionRequest

DEFAULT_REPORT_FILE = Path("tool_safety_report.json")


class SafetyReportWriter:
    """Write the latest tool safety result as a stable JSON report."""

    def __init__(self, path: str | Path = DEFAULT_REPORT_FILE):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Return the report path."""
        return self._path

    def write(self, result: SafetyResult) -> None:
        """Write one report file."""
        report = build_report(result)
        with self._path.open("w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2, sort_keys=True)
            fp.write("\n")


def build_report(result: SafetyResult) -> dict[str, Any]:
    """Build a stable, monitor-friendly report object."""
    request = result.request or ToolExecutionRequest()
    findings = result.findings or []
    rule_ids = [finding.rule_id for finding in findings]
    return {
        "schema_version": "v1",
        "decision": result.decision.value,
        "risk_level": risk_level(findings).value,
        "rule_id": ",".join(rule_ids),
        "rule_ids": rule_ids,
        "evidence": [_evidence(finding) for finding in findings],
        "recommendation": _recommendation(result),
        "tool_name": request.tool_name,
        "agent_name": request.agent_name,
        "invocation_id": request.invocation_id,
        "function_call_id": request.function_call_id,
        "language": request.language,
        "finding_count": len(findings),
        "blocked": result.decision != SafetyDecision.ALLOW,
    }


def _evidence(finding: Finding) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity.value,
        "message": finding.message,
        "target": finding.target,
        "metadata": finding.metadata,
    }


def _recommendation(result: SafetyResult) -> str:
    if result.decision == SafetyDecision.DENY:
        return "Do not execute this tool call. Review the reported rule findings and modify the script or policy."
    if result.decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
        return "Require human review before executing this tool call."
    return "No blocking safety findings. Continue execution."
