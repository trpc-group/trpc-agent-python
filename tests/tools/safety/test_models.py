from datetime import timezone

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.tools.safety._models import RiskCategory
from trpc_agent_sdk.tools.safety._models import RiskLevel
from trpc_agent_sdk.tools.safety._models import SafetyDecision
from trpc_agent_sdk.tools.safety._models import SafetyFinding
from trpc_agent_sdk.tools.safety._models import SafetyScanRequest
from trpc_agent_sdk.tools.safety._models import ScriptLanguage
from trpc_agent_sdk.tools.safety._models import highest_risk_level
from trpc_agent_sdk.tools.safety._models import strictest_decision


def _finding(*, risk_level=RiskLevel.MEDIUM, decision=SafetyDecision.NEEDS_HUMAN_REVIEW):
    return SafetyFinding(
        rule_id="TEST001",
        category=RiskCategory.POLICY_VIOLATION,
        risk_level=risk_level,
        decision=decision,
        evidence="test evidence",
        recommendation="review it",
    )


def test_clean_findings_are_low_risk_and_allowed():
    assert highest_risk_level([]) is RiskLevel.LOW
    assert strictest_decision([]) is SafetyDecision.ALLOW


def test_finding_aggregation_selects_strictest_values():
    findings = [
        _finding(),
        _finding(risk_level=RiskLevel.CRITICAL, decision=SafetyDecision.DENY),
        _finding(risk_level=RiskLevel.HIGH, decision=SafetyDecision.ALLOW),
    ]

    assert highest_risk_level(findings) is RiskLevel.CRITICAL
    assert strictest_decision(findings) is SafetyDecision.DENY


def test_scan_request_discards_environment_values():
    request = SafetyScanRequest.from_execution(
        script="print('ok')",
        language="python",
        environment={
            "API_KEY": "do-not-retain",
            "PATH": "/bin"
        },
    )

    assert request.environment_keys == ["API_KEY", "PATH"]
    assert "do-not-retain" not in request.model_dump_json()


def test_scan_request_is_strict_and_frozen():
    with pytest.raises(ValidationError):
        SafetyScanRequest(script="pass", language=ScriptLanguage.PYTHON, unknown=True)

    request = SafetyScanRequest(script="pass", language=ScriptLanguage.PYTHON)
    with pytest.raises(ValidationError):
        request.script = "changed"


def test_finding_timestamp_and_location_validation():
    with pytest.raises(ValidationError):
        SafetyFinding(
            rule_id="",
            category=RiskCategory.SCAN_ERROR,
            risk_level=RiskLevel.MEDIUM,
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            evidence="x",
            recommendation="y",
            line_number=0,
        )

    from trpc_agent_sdk.tools.safety._models import SafetyAuditEvent

    event = SafetyAuditEvent(
        tool_name="runner",
        decision=SafetyDecision.ALLOW,
        risk_level=RiskLevel.LOW,
        duration_ms=0,
        redacted=True,
        blocked=False,
        script_sha256="0" * 64,
        policy_version="1",
    )
    assert event.timestamp.tzinfo is timezone.utc
