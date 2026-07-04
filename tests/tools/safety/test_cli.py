import json
import subprocess
import sys
from pathlib import Path

import yaml

SAMPLES = Path("examples/tool_safety/samples")
CLI = Path("scripts/tool_safety_check.py")


def run_cli(*args):
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, check=False)


def test_scans_file():
    result = run_cli("--file", str(SAMPLES / "safe_bash.sh"), "--language", "bash")
    assert result.returncode == 0
    assert json.loads(result.stdout)["decision"] == "allow"


def test_writes_output_json(tmp_path):
    output = tmp_path / "report.json"
    result = run_cli("--file", str(SAMPLES / "dangerous_delete.sh"), "--language", "bash", "--output", str(output))
    assert result.returncode == 3
    assert json.loads(output.read_text())["decision"] == "deny"


def test_writes_audit_jsonl(tmp_path):
    audit = tmp_path / "audit.jsonl"
    result = run_cli("--file", str(SAMPLES / "dangerous_delete.sh"), "--language", "bash", "--audit-log", str(audit))
    assert result.returncode == 3
    assert len(audit.read_text().splitlines()) == 1


def test_exit_code_mapping():
    assert run_cli("--file", str(SAMPLES / "safe_python.py")).returncode == 0
    assert run_cli("--file", str(SAMPLES / "eval_review.py")).returncode == 2
    assert run_cli("--file", str(SAMPLES / "dangerous_delete.sh")).returncode == 3


def test_positional_file_argument_supported():
    result = run_cli(str(SAMPLES / "safe_bash.sh"), "--language", "bash")
    assert result.returncode == 0
    assert json.loads(result.stdout)["decision"] == "allow"


def test_strict_policy_invalid_policy_exits_one(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(yaml.safe_dump({"allowed_domans": ["api.example.com"]}), encoding="utf-8")
    result = run_cli(
        "--file",
        str(SAMPLES / "safe_bash.sh"),
        "--language",
        "bash",
        "--policy",
        str(policy),
        "--strict-policy",
    )
    assert result.returncode == 1
    assert "unknown policy key" in result.stderr
