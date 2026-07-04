import json

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


def test_report_has_required_fields():
    report = ToolScriptSafetyScanner().scan_script("cat .env", "bash")
    data = report.to_dict()
    assert data["decision"] == Decision.DENY.value
    assert data["risk_level"] == "high"
    assert data["findings"][0]["rule_id"]
    assert data["findings"][0]["evidence"]
    assert data["findings"][0]["recommendation"]


def test_report_is_json_serializable():
    report = ToolScriptSafetyScanner().scan_script("echo ok", "bash")
    json.dumps(report.to_dict())


def test_evidence_is_sanitized():
    secret = "raw_private_key_material"
    report = ToolScriptSafetyScanner().scan_script(
        f'key = """-----BEGIN PRIVATE KEY-----\n{secret}\n-----END PRIVATE KEY-----"""',
        "python",
    )
    assert report.sanitized
    assert secret not in json.dumps(report.to_dict())
