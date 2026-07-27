# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public sample corpus and CLI acceptance tests."""

from pathlib import Path

import yaml

from trpc_agent_sdk.tools.safety import adapt_cli_request
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard
from trpc_agent_sdk.tools.safety._cli import main
from trpc_agent_sdk.tools.safety._cli import _exit_code
from trpc_agent_sdk.tools.safety._audit import SafetyAuditError
from trpc_agent_sdk.tools.safety import SafetyDecision

EXAMPLE_DIR = Path("examples/tool_safety_guard")


def _results():
    manifest = yaml.safe_load((EXAMPLE_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    guard = ToolScriptSafetyGuard.from_policy(str(EXAMPLE_DIR / "tool_safety_policy.yaml"))
    results = {}
    for item in manifest["samples"]:
        path = EXAMPLE_DIR / item["file"]
        payload = ScriptPayload(
            language=ScriptLanguage(item["language"]),
            content=path.read_text(encoding="utf-8"),
            source=str(path),
        )
        request = adapt_cli_request(payload, ToolMetadata(name="acceptance"), guard.policy, str(EXAMPLE_DIR))
        results[item["file"]] = (item, guard.scan(request))
    return results


def test_public_sample_decisions_and_rates():
    results = _results()
    assert len(results) == 12
    for item, report in results.values():
        assert report.decision.value == item["expected"]

    safe = [report for item, report in results.values() if item.get("safe")]
    dangerous = [report for item, report in results.values() if not item.get("safe")]
    false_positive_rate = sum(report.decision.value != "allow" for report in safe) / len(safe)
    detection_rate = sum(report.decision.value != "allow" for report in dangerous) / len(dangerous)
    assert false_positive_rate <= 0.10
    assert detection_rate >= 0.90


def test_mandatory_categories_have_full_detection():
    results = _results()
    mandatory = [
        "samples/danger_delete.py",
        "samples/danger_ssh.py",
        "samples/danger_network.py",
    ]
    assert all(results[name][1].decision.value == "deny" for name in mandatory)


def test_cli_writes_report_and_audit(tmp_path, capsys):
    report = tmp_path / "report.json"
    audit = tmp_path / "audit.jsonl"
    exit_code = main([
        "--file",
        str(EXAMPLE_DIR / "samples/danger_delete.py"),
        "--language",
        "python",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
        "--report",
        str(report),
        "--audit",
        str(audit),
    ])
    assert exit_code == 3
    assert '"decision": "deny"' in report.read_text(encoding="utf-8")
    assert '"execution_blocked":true' in audit.read_text(encoding="utf-8")
    assert '"decision": "deny"' in capsys.readouterr().out


def test_cli_scans_argv_and_accepts_metadata(tmp_path):
    report = tmp_path / "report.json"
    exit_code = main([
        "--command",
        "echo ok",
        "--language",
        "bash",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
        "--report",
        str(report),
        "--argv",
        "~/.ssh/id_rsa",
        "--env-key",
        "TOKEN",
        "--tool-description",
        "MCP command",
        "--tag",
        "mcp",
    ])
    assert exit_code == 3


def test_cli_error_redacts_secret_path(tmp_path, capsys):
    missing = tmp_path / "password='top secret phrase'" / "missing.py"
    exit_code = main([
        "--file",
        str(missing),
        "--language",
        "python",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
    ])
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "top secret phrase" not in output
    assert "[REDACTED_SECRET]" in output


def test_cli_audit_failure_returns_structured_error(monkeypatch, capsys):

    def fail_audit(*args, **kwargs):
        del args, kwargs
        raise SafetyAuditError("audit unavailable")

    monkeypatch.setattr("trpc_agent_sdk.tools.safety._cli.emit_report", fail_audit)
    exit_code = main([
        "--command",
        "echo ok",
        "--language",
        "bash",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
        "--audit",
        "audit.jsonl",
    ])
    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"error"' in output
    assert "Traceback" not in output


def test_cli_exit_codes_cover_allow_and_review():
    assert _exit_code(SafetyDecision.ALLOW) == 0
    assert _exit_code(SafetyDecision.NEEDS_HUMAN_REVIEW) == 2
