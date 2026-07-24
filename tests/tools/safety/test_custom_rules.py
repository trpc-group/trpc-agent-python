import json

import pytest

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import RiskFinding
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import clear_custom_safety_rules
from trpc_agent_sdk.tools.safety import register_safety_rule
from trpc_agent_sdk.tools.safety import unregister_safety_rule
from trpc_agent_sdk.tools.safety._audit import write_audit_event


@pytest.fixture(autouse=True)
def reset_custom_rules():
    clear_custom_safety_rules()
    yield
    clear_custom_safety_rules()


def custom_finding(rule_id="CUSTOM_BLOCKED"):
    return RiskFinding(
        rule_id=rule_id,
        risk_type="custom",
        risk_level=RiskLevel.HIGH,
        decision=Decision.DENY,
        evidence="custom marker detected",
        recommendation="Remove the custom marker.",
        message="Custom rule matched.",
    )


def test_registered_rule_matches_script():
    def rule(context):
        if "CUSTOM_MARKER" in context.script:
            return [custom_finding()]
        return []

    register_safety_rule("marker", rule, languages=["python"])
    report = ToolScriptSafetyScanner().scan_script("print('CUSTOM_MARKER')", "python")

    assert report.decision == Decision.DENY
    assert "CUSTOM_BLOCKED" in {finding.rule_id for finding in report.findings}


def test_unregistered_rule_no_longer_matches():
    register_safety_rule("marker", lambda context: [custom_finding()], languages=["bash"])
    unregister_safety_rule("marker")

    report = ToolScriptSafetyScanner().scan_script("echo CUSTOM_MARKER", "bash")

    assert "CUSTOM_BLOCKED" not in {finding.rule_id for finding in report.findings}


def test_exception_rule_returns_review_finding():
    def broken_rule(context):
        raise RuntimeError("boom secret=super_secret_token_value")

    register_safety_rule("broken", broken_rule)
    report = ToolScriptSafetyScanner().scan_script("echo ok", "bash")

    assert report.decision == Decision.NEEDS_HUMAN_REVIEW
    assert "CUSTOM_RULE_ERROR" in {finding.rule_id for finding in report.findings}
    assert "super_secret_token_value" not in str(report.to_dict())


def test_custom_rule_finding_enters_audit_and_aggregation(tmp_path):
    register_safety_rule("marker", lambda context: [custom_finding("CUSTOM_AUDIT")])
    report = ToolScriptSafetyScanner().scan_script("echo ok", "bash")
    audit_path = tmp_path / "audit.jsonl"

    write_audit_event(report, str(audit_path))
    event = json.loads(audit_path.read_text(encoding="utf-8"))

    assert report.decision == Decision.DENY
    assert "CUSTOM_AUDIT" in event["rule_ids"]
