from pathlib import Path

import yaml

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

SAMPLES = Path("examples/tool_safety/samples")
MANIFEST = SAMPLES / "manifest.yaml"


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
