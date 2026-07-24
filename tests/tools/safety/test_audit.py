# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

import json

from trpc_agent_sdk.tools.safety._audit import AuditRecord
from trpc_agent_sdk.tools.safety._audit import build_audit_record
from trpc_agent_sdk.tools.safety._audit import emit_otel
from trpc_agent_sdk.tools.safety._audit import record_safety_decision
from trpc_agent_sdk.tools.safety._audit import write_audit
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import Finding
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import SafetyReport


def _report(decision=Decision.DENY, risk=RiskLevel.HIGH, findings=None, duration=7):
    return SafetyReport(
        decision=decision,
        risk_level=risk,
        findings=findings or [
            Finding(
                rule_id="tool-fs-recursive-delete",
                risk_level=RiskLevel.HIGH,
                rule_decision=Decision.DENY,
                evidence="rm -rf /",
                recommendation="refuse",
            )
        ],
        recommendation="blocked",
        scan_duration_ms=duration,
        sanitized=False,
    )


def test_build_audit_record_has_all_issue_required_fields():
    rec = build_audit_record(
        _report(), tool_name="weather_tool", language="python", intercepted=True
    )
    # Issue #90 mandatory audit fields:
    assert rec.tool_name == "weather_tool"          # tool name
    assert rec.decision == "DENY"                    # decision
    assert rec.risk_level == "HIGH"                  # risk level
    assert rec.rule_ids == ["tool-fs-recursive-delete"]  # rule id
    assert rec.scan_duration_ms == 7                 # duration
    assert rec.sanitized is False                    # sanitized flag
    assert rec.intercepted is True                   # intercepted flag
    assert rec.timestamp  # non-empty ISO timestamp


def test_write_audit_appends_jsonl_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    rec = AuditRecord(
        timestamp="2026-07-11T00:00:00+00:00",
        tool_name="t",
        language="python",
        decision="DENY",
        risk_level="HIGH",
        rule_ids=["tool-fs-recursive-delete"],
        scan_duration_ms=1,
        sanitized=False,
        intercepted=True,
    )
    write_audit(rec, str(path))
    write_audit(rec, str(path))
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    obj = json.loads(lines[0])
    assert obj["tool_name"] == "t"
    assert obj["decision"] == "DENY"
    assert obj["intercepted"] is True


def test_record_safety_decision_writes_and_returns(tmp_path):
    path = tmp_path / "a.jsonl"
    rec = record_safety_decision(
        _report(decision=Decision.ALLOW, risk=RiskLevel.NONE, findings=[], duration=2),
        tool_name="safe_tool",
        language="bash",
        intercepted=False,
        audit_path=str(path),
    )
    assert rec.intercepted is False
    assert rec.decision == "ALLOW"
    assert path.exists()
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert line["tool_name"] == "safe_tool"


def test_emit_otel_is_safe_without_active_span():
    # No active OTel span in unit tests -> must be a no-op, never raise.
    emit_otel(_report(), tool_name="t", intercepted=True)
