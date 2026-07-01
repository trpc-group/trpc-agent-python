"""Tests for the standalone Tool Safety scanner CLI."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest


def _load_cli_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "tool_safety_check.py"
    spec = importlib.util.spec_from_file_location("tool_safety_check", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cli_allows_safe_python_script(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    script = tmp_path / "safe.py"
    script.write_text("print('hello')\n", encoding="utf-8")

    exit_code = cli.main([str(script)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["decision"] == "allow"
    assert output["risk_level"] == "none"
    assert output["rule_id"] == "safe_python"


def test_cli_allows_safe_bash_script_text_format(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    script = tmp_path / "safe.sh"
    script.write_text("echo hello\n", encoding="utf-8")

    exit_code = cli.main([str(script), "--format", "text"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "decision: allow" in output
    assert "risk_level: none" in output
    assert "rule_id: safe_python" in output


def test_cli_returns_one_for_deny(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    script = tmp_path / "danger.sh"
    script.write_text("rm -rf /tmp/demo\n", encoding="utf-8")

    exit_code = cli.main([str(script)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["decision"] == "deny"
    assert output["risk_level"] == "critical"
    assert output["rule_id"] == "dangerous_delete"
    assert output["evidence"]
    assert output["recommendation"]


def test_cli_returns_two_for_needs_human_review(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    script = tmp_path / "install.sh"
    script.write_text("npm install left-pad\n", encoding="utf-8")

    exit_code = cli.main([str(script)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["decision"] == "needs_human_review"
    assert output["rule_id"] == "npm_install"


def test_cli_loads_policy_and_writes_json_report(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    script = tmp_path / "fetch.sh"
    policy = tmp_path / "policy.yaml"
    report_file = tmp_path / "reports" / "tool_safety.json"
    script.write_text("curl https://api.example.com/data\n", encoding="utf-8")
    policy.write_text(
        """
allowed_domains:
  - api.example.com
""",
        encoding="utf-8",
    )

    exit_code = cli.main([str(script), "--policy", str(policy), "--output", str(report_file)])

    stdout_report = json.loads(capsys.readouterr().out)
    file_report = json.loads(report_file.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert stdout_report["decision"] == "allow"
    assert stdout_report["rule_id"] == "network_allowlist"
    assert file_report == stdout_report


def test_cli_rejects_explicit_missing_policy_file(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    script = tmp_path / "safe.py"
    missing_policy = tmp_path / "missing.yaml"
    script.write_text("print('hello')\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli.main([str(script), "--policy", str(missing_policy)])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "policy file not found" in captured.err
    assert str(missing_policy) in captured.err


def test_cli_scans_500_line_script_under_one_second(tmp_path, capsys) -> None:
    cli = _load_cli_module()
    script = tmp_path / "large_safe.py"
    script.write_text("\n".join("print('hello')" for _ in range(500)) + "\n", encoding="utf-8")

    started_at = time.perf_counter()
    exit_code = cli.main([str(script)])
    elapsed = time.perf_counter() - started_at

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["decision"] == "allow"
    assert elapsed <= 1.0
