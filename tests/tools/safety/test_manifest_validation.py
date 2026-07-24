from collections import defaultdict
from pathlib import Path

import pytest
import yaml

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

SAMPLES = Path("examples/tool_safety/samples")
MANIFEST = SAMPLES / "manifest.yaml"
POLICY = Path("examples/tool_safety/tool_safety_policy.yaml")
REPORT_FIELDS = {
    "scan_id",
    "timestamp",
    "decision",
    "risk_level",
    "findings",
    "summary",
    "telemetry_attributes",
}
SECRET_NEEDLES = {
    "dont_log_this_secret",
    "dont_show_this_secret_value",
    "super_secret_token_value",
    "raw_password_value",
    "plaintext_env_value",
}


def load_manifest():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["samples"]


@pytest.mark.parametrize("sample", load_manifest(), ids=lambda sample: sample["file"])
def test_manifest_sample_decision_rule_and_report_shape(sample):
    scanner = ToolScriptSafetyScanner(ToolSafetyPolicy.from_file(POLICY))
    report = scanner.scan_file(str(SAMPLES / sample["file"]), language=sample["language"])
    report_dict = report.to_dict()
    rule_ids = {finding.rule_id for finding in report.findings}

    assert report.decision == Decision(sample["expected_decision"]), (
        f"{sample['file']}: expected {sample['expected_decision']}, "
        f"actual {report.decision.value}, rules={sorted(rule_ids)}"
    )
    required_rule = sample.get("required_rule_id")
    if required_rule and required_rule != "NONE":
        assert required_rule in rule_ids, (
            f"{sample['file']}: expected {sample['expected_decision']}, "
            f"actual {report.decision.value}, missing rule_id={required_rule}, "
            f"rules={sorted(rule_ids)}"
        )

    assert REPORT_FIELDS <= set(report_dict), f"{sample['file']}: missing report fields"
    for finding in report.findings:
        assert finding.rule_id
        assert finding.recommendation
        assert finding.evidence == finding.evidence.replace("\n", "\\n")
        for needle in SECRET_NEEDLES:
            assert needle not in finding.evidence


def test_manifest_category_acceptance_summary():
    scanner = ToolScriptSafetyScanner(ToolSafetyPolicy.from_file(POLICY))
    grouped = defaultdict(list)
    for sample in load_manifest():
        report = scanner.scan_file(str(SAMPLES / sample["file"]), language=sample["language"])
        grouped[sample["category"]].append((sample, report))

    for sample, report in grouped["secret_read"]:
        assert report.decision != Decision.ALLOW, f"{sample['file']} unexpectedly allowed"
    for sample, report in grouped["dangerous_delete"]:
        assert report.decision == Decision(
            sample["expected_decision"]
        ), f"{sample['file']}: expected {sample['expected_decision']}, actual {report.decision.value}"
    for sample, report in grouped["network_non_whitelist"]:
        assert report.decision == Decision(
            sample["expected_decision"]
        ), f"{sample['file']}: expected {sample['expected_decision']}, actual {report.decision.value}"
    for category, entries in grouped.items():
        if category.startswith("safe"):
            for sample, report in entries:
                assert report.decision != Decision.DENY, f"{sample['file']} safe sample denied"
