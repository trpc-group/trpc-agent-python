#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

import json
from pathlib import Path
import subprocess
import sys

from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ScriptScanRequest
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import load_policy

ROOT = Path(__file__).parents[3]
EXAMPLE_DIR = ROOT / "examples" / "tool_safety"
SAMPLES = EXAMPLE_DIR / "samples"


def _language(path: Path) -> ScriptLanguage:
    return ScriptLanguage.PYTHON if path.suffix == ".py" else ScriptLanguage.BASH


def test_public_manifest_decisions_and_acceptance_rates():
    manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
    scanner = ToolScriptSafetyScanner(load_policy(EXAMPLE_DIR / "tool_safety_policy.yaml"))
    dangerous_detected = 0
    dangerous_total = 0
    safe_false_positives = 0
    safe_total = 0

    for sample in manifest:
        path = SAMPLES / sample["file"]
        report = scanner.scan(
            ScriptScanRequest(
                payloads=[ScriptPayload(language=_language(path), content=path.read_text(encoding="utf-8"))],
                metadata=ToolMetadata(name=path.name),
            )
        )
        assert report.decision.value == sample["decision"]
        if sample["rule_id"]:
            assert sample["rule_id"] in report.rule_ids
        if sample["kind"] == "dangerous":
            dangerous_total += 1
            dangerous_detected += report.decision != SafetyDecision.ALLOW
        elif sample["kind"] == "safe":
            safe_total += 1
            safe_false_positives += report.decision != SafetyDecision.ALLOW

    assert dangerous_detected / dangerous_total >= 0.90
    assert safe_false_positives / safe_total <= 0.10


def test_cli_writes_structured_report_and_redacted_audit(tmp_path):
    report_path = tmp_path / "report.json"
    audit_path = tmp_path / "audit.jsonl"
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "tool_safety_check.py"),
            "--policy",
            str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
            "--output",
            str(report_path),
            "--audit",
            str(audit_path),
            str(SAMPLES / "01_safe_python.py"),
            str(SAMPLES / "02_dangerous_delete.py"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 3
    output = json.loads(report_path.read_text(encoding="utf-8"))
    assert output["schema_version"] == "1"
    assert [item["decision"] for item in output["reports"]] == ["allow", "deny"]
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2
    assert events[1]["execution_blocked"] is True
    assert "shutil.rmtree" not in audit_path.read_text(encoding="utf-8")


def test_checked_in_report_and_audit_examples_match_current_scanner():
    output = json.loads((EXAMPLE_DIR / "tool_safety_report.json").read_text(encoding="utf-8"))
    policy = load_policy(EXAMPLE_DIR / "tool_safety_policy.yaml")
    scanner = ToolScriptSafetyScanner(policy)
    for item in output["reports"]:
        expected = SafetyReport.model_validate({key: value for key, value in item.items() if key != "path"})
        path = ROOT / item["path"]
        actual = scanner.scan(
            ScriptScanRequest(
                payloads=[ScriptPayload(language=_language(path), content=path.read_text(encoding="utf-8"))],
                metadata=ToolMetadata(name=path.name),
            )
        )
        expected_data = expected.model_dump(mode="json")
        actual_data = actual.model_dump(mode="json")
        expected_data.pop("duration_ms")
        actual_data.pop("duration_ms")
        assert actual_data == expected_data

    events = [
        SafetyAuditEvent.model_validate_json(line)
        for line in (EXAMPLE_DIR / "tool_safety_audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == len(output["reports"])
    for event, item in zip(events, output["reports"]):
        assert event.tool_name == Path(item["path"]).name
        assert event.decision.value == item["decision"]
        assert event.risk_level.value == item["risk_level"]
        assert event.rule_ids == item["rule_ids"]
        assert event.redacted == item["redacted"]
        assert event.execution_blocked == (item["decision"] != "allow")
        assert event.policy_version == item["policy_version"]
