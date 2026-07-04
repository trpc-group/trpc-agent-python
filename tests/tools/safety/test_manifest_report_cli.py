import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path("scripts/tool_safety_manifest_report.py")
SAMPLES = Path("examples/tool_safety/samples")
POLICY = Path("examples/tool_safety/tool_safety_policy.yaml")


def run_report(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def write_manifest(tmp_path, samples):
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"samples": samples}), encoding="utf-8")
    return path


def test_manifest_report_current_manifest_exits_zero(tmp_path):
    output = tmp_path / "all_reports.json"
    result = run_report("--policy", str(POLICY), "--output", str(output), "--strict-policy")

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["sample_count"] == summary["matched_decisions"]
    assert summary["sample_count"] == summary["required_rules_present"]
    assert output.exists()


def test_manifest_report_output_is_deterministic(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    first_result = run_report("--policy", str(POLICY), "--output", str(first), "--strict-policy")
    second_result = run_report("--policy", str(POLICY), "--output", str(second), "--strict-policy")

    assert first_result.returncode == 0
    assert second_result.returncode == 0
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_manifest_report_decision_mismatch_exits_one(tmp_path):
    output = tmp_path / "all_reports.json"
    manifest = write_manifest(
        tmp_path,
        [
            {
                "file": "safe_bash.sh",
                "language": "bash",
                "expected_decision": "deny",
                "required_rule_id": "NONE",
                "category": "safe_local",
                "high_risk": False,
            }
        ],
    )

    result = run_report(
        "--manifest",
        str(manifest),
        "--samples-dir",
        str(SAMPLES),
        "--policy",
        str(POLICY),
        "--output",
        str(output),
    )

    assert result.returncode == 1
    assert "safe_bash.sh" in result.stdout
    assert "expected_decision=deny" in result.stdout
    assert "actual_decision=allow" in result.stdout
    assert "FAIL safe_bash.sh expected_decision=deny actual_decision=allow" in result.stdout
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["failures"] == [
        {
            "file": "safe_bash.sh",
            "expected_decision": "deny",
            "actual_decision": "allow",
            "required_rule_id": "NONE",
            "actual_rule_ids": [],
        }
    ]


def test_manifest_report_missing_required_rule_exits_one(tmp_path):
    output = tmp_path / "all_reports.json"
    manifest = write_manifest(
        tmp_path,
        [
            {
                "file": "dangerous_delete.sh",
                "language": "bash",
                "expected_decision": "deny",
                "required_rule_id": "MISSING_RULE",
                "category": "dangerous_delete",
                "high_risk": True,
            }
        ],
    )

    result = run_report(
        "--manifest",
        str(manifest),
        "--samples-dir",
        str(SAMPLES),
        "--policy",
        str(POLICY),
        "--output",
        str(output),
    )

    assert result.returncode == 1
    assert "dangerous_delete.sh" in result.stdout
    assert "required_rule_id=MISSING_RULE" in result.stdout
    assert "actual_rule_ids=" in result.stdout
    assert "FAIL dangerous_delete.sh" in result.stdout
    assert "actual_rule_ids=[" in result.stdout
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["failures"][0]["file"] == "dangerous_delete.sh"
    assert data["failures"][0]["required_rule_id"] == "MISSING_RULE"
    assert "BASH_DANGEROUS_RM_RF" in data["failures"][0]["actual_rule_ids"]


def test_manifest_report_strict_policy_error_exits_one(tmp_path):
    policy = tmp_path / "policy.yaml"
    output = tmp_path / "all_reports.json"
    policy.write_text(yaml.safe_dump({"allowed_domans": ["typo-only.example"]}), encoding="utf-8")

    result = run_report("--policy", str(policy), "--strict-policy", "--output", str(output))

    assert result.returncode == 1
    assert "unknown policy key" in result.stderr
