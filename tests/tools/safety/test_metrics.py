from pathlib import Path

import yaml

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

SAMPLES = Path("examples/tool_safety/samples")
MANIFEST = SAMPLES / "manifest.yaml"
ALL_REPORTS = Path("examples/tool_safety/all_reports.json")


def load_manifest():
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return data["samples"]


def test_sample_matrix_metrics():
    scanner = ToolScriptSafetyScanner()
    matrix = load_manifest()
    assert len(matrix) >= 30

    actual = {}
    for sample in matrix:
        report = scanner.scan_file(str(SAMPLES / sample["file"]), language=sample["language"])
        actual[sample["file"]] = report.decision
        assert report.decision == Decision(sample["expected_decision"])
        if sample["required_rule_id"] != "NONE":
            assert sample["required_rule_id"] in {finding.rule_id for finding in report.findings}

    high_risk = [sample["file"] for sample in matrix if sample["high_risk"]]
    detected = [sample for sample in high_risk if actual[sample] == Decision.DENY]
    assert len(detected) / len(high_risk) >= 0.9

    safe = [sample["file"] for sample in matrix if sample["expected_decision"] == Decision.ALLOW.value]
    false_positive = [sample for sample in safe if actual[sample] != Decision.ALLOW]
    assert len(false_positive) / len(safe) <= 0.1

    for sample in ("read_env.py", "dangerous_delete.sh", "network_non_whitelist.py"):
        assert actual[sample] == Decision.DENY


def test_all_reports_matches_manifest_and_current_scanner():
    scanner = ToolScriptSafetyScanner()
    matrix = load_manifest()
    reports_data = yaml.safe_load(ALL_REPORTS.read_text(encoding="utf-8"))
    reports = reports_data["reports"]

    manifest_by_file = {sample["file"]: sample for sample in matrix}
    reports_by_file = {report["file"]: report for report in reports}
    assert reports_data["sample_count"] == len(matrix)
    assert set(reports_by_file) == set(manifest_by_file)

    matched_decisions = 0
    required_rules_present = 0
    for file_name, sample in manifest_by_file.items():
        report_entry = reports_by_file[file_name]
        report = scanner.scan_file(str(SAMPLES / file_name), language=sample["language"])
        rule_ids = {finding.rule_id for finding in report.findings}

        assert report_entry["language"] == sample["language"]
        assert report_entry["category"] == sample["category"]
        assert report_entry["high_risk"] == sample["high_risk"]
        assert report_entry["expected_decision"] == sample["expected_decision"]
        assert report_entry["actual_decision"] == report.decision.value
        assert report_entry["report"]["decision"] == report.decision.value

        if report.decision.value == sample["expected_decision"]:
            matched_decisions += 1
        required_rule = sample["required_rule_id"]
        required_present = required_rule == "NONE" or required_rule in rule_ids
        assert report_entry["required_rule_id"] == required_rule
        assert report_entry["required_rule_present"] == required_present
        if required_present:
            required_rules_present += 1

    assert reports_data["matched_decisions"] == matched_decisions
    assert reports_data["required_rules_present"] == required_rules_present
