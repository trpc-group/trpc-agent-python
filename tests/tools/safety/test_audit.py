import json

from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety._audit import write_audit_event


def test_writes_one_jsonl_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    report = ToolScriptSafetyScanner().scan_script("cat .env", "bash", tool_name="Bash")
    write_audit_event(report, str(path))
    lines = path.read_text().splitlines()
    assert len(lines) == 1


def test_audit_fields_and_secret_redaction(tmp_path):
    path = tmp_path / "audit.jsonl"
    secret = "dont_log_this_secret"
    report = ToolScriptSafetyScanner().scan_script(
        f'key = """-----BEGIN PRIVATE KEY-----\n{secret}\n-----END PRIVATE KEY-----"""',
        "python",
        tool_name="unit",
    )
    write_audit_event(report, str(path))
    event = json.loads(path.read_text())
    assert event["tool_name"] == "unit"
    assert event["decision"] == "deny"
    assert event["risk_level"] == "critical"
    assert event["rule_ids"]
    assert "elapsed_ms" in event
    assert event["sanitized"] is True
    assert event["blocked"] is True
    assert secret not in path.read_text()
