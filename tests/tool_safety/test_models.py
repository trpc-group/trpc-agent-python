"""Tests for data models and invariants."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.tools.safety._models import (
    SAFE_RULE_ID,
    Evidence,
    RiskCategory,
    RiskLevel,
    SafetyDecision,
    SafetyFinding,
    SafetyReport,
    SafetyScanRequest,
    ScriptLanguage,
    ToolKind,
)


def test_request_repr_hides_script_and_env():
    request = SafetyScanRequest(
        tool_name="t",
        language=ScriptLanguage.PYTHON,
        script="secret-script-body",
        env={"API_TOKEN": "abc123"},
    )
    rendered = repr(request)
    assert "secret-script-body" not in rendered
    assert "abc123" not in rendered


def test_frozen_report_immutable():
    report = SafetyReport(
        report_id="r",
        decision=SafetyDecision.ALLOW,
        risk_level=RiskLevel.INFO,
        rule_ids=(SAFE_RULE_ID,),
        findings=(),
        recommendation="ok",
        policy_hash="p",
        policy_version="1",
        script_sha256="s",
        scan_duration_ms=1.0,
        redacted=False,
    )
    with pytest.raises(Exception):
        report.decision = SafetyDecision.DENY  # type: ignore[misc]


def test_report_json_uses_label_for_risk_level():
    report = SafetyReport(
        report_id="r",
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.CRITICAL,
        rule_ids=("FILE001_RECURSIVE_DELETE",),
        findings=(),
        recommendation="x",
        policy_hash="p",
        policy_version="1",
        script_sha256="s",
        scan_duration_ms=2.0,
        redacted=True,
    )
    data = json.loads(report.model_dump_json())
    assert data["risk_level"] == "critical"
    assert data["decision"] == "deny"
    obj_keys = set(data.keys())
    assert "script" not in obj_keys
    assert "argv" not in obj_keys
    assert "env" not in obj_keys
    assert "cwd" not in obj_keys


def test_extra_forbid_on_request():
    with pytest.raises(ValidationError):
        SafetyScanRequest(
            tool_name="t",
            language=ScriptLanguage.PYTHON,
            random_field="oops",  # type: ignore[call-arg]
        )


def test_combine_reports_takes_worst_decision():
    allow_report = SafetyReport(
        report_id="a",
        decision=SafetyDecision.ALLOW,
        risk_level=RiskLevel.INFO,
        rule_ids=(SAFE_RULE_ID,),
        findings=(),
        recommendation="",
        policy_hash="p",
        policy_version="1",
        script_sha256="x",
        scan_duration_ms=0.1,
        redacted=False,
    )
    deny_report = SafetyReport(
        report_id="b",
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.CRITICAL,
        rule_ids=("FILE001_RECURSIVE_DELETE",),
        findings=(
            SafetyFinding(
                rule_id="FILE001_RECURSIVE_DELETE",
                category=RiskCategory.FILE,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence=Evidence(snippet="x"),
                recommendation="d",
            ),
        ),
        recommendation="d",
        policy_hash="p",
        policy_version="1",
        script_sha256="y",
        scan_duration_ms=0.2,
        redacted=False,
    )
    combined = SafetyReport.combine(
        [allow_report, deny_report],
        report_id="c",
        policy_hash="p",
        policy_version="1",
        scan_duration_ms=0.3,
    )
    assert combined.decision == SafetyDecision.DENY
    assert combined.risk_level == RiskLevel.CRITICAL


def test_combine_empty_yields_allow():
    combined = SafetyReport.combine(
        [],
        report_id="c",
        policy_hash="p",
        policy_version="1",
        scan_duration_ms=0.0,
    )
    assert combined.decision == SafetyDecision.ALLOW
    assert combined.rule_ids == (SAFE_RULE_ID,)
