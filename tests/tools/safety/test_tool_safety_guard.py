# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyRiskLevel
from trpc_agent_sdk.tools.safety import ToolSafetyAuditLogger
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanRequest
from trpc_agent_sdk.tools.safety import ToolSafetyScanner
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import create_code_execution_result
from scripts.tool_safety_check import main as tool_safety_check_main


EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "tool_safety_guard"
SAMPLES = EXAMPLE_DIR / "samples"


def policy(**kwargs):
    data = {
        "name": "test-policy",
        "allowed_domains": ["api.github.com"],
        "allowed_commands": ["cat", "echo", "grep", "python", "python3", "bash", "ls", "tee"],
        "denied_paths": ["~/.ssh", ".env", "id_rsa", ".aws/credentials"],
        "deny_risk_level": "high",
        "review_risk_level": "medium",
        "block_on_review": True,
        "max_sleep_seconds": 60,
        "max_loop_iterations": 10000,
    }
    data.update(kwargs)
    return ToolSafetyPolicy.from_mapping(data)


def scan_sample(name: str):
    path = SAMPLES / name
    language = "bash" if path.suffix == ".sh" else "python"
    scanner = ToolSafetyScanner(policy())
    return scanner.scan(ToolSafetyScanRequest(script=path.read_text(encoding="utf-8"), language=language))


@pytest.mark.parametrize(
    ("sample", "decision", "rule_id"),
    [
        ("01_safe_python.py", SafetyDecision.ALLOW, None),
        ("02_dangerous_delete.sh", SafetyDecision.DENY, "TSG-FILE-RECURSIVE-DELETE"),
        ("03_read_secret.py", SafetyDecision.DENY, "TSG-FILE-DENIED-PATH"),
        ("04_network_external.py", SafetyDecision.DENY, "TSG-NETWORK-NONALLOWLIST"),
        ("05_network_allowlist.py", SafetyDecision.ALLOW, "TSG-NETWORK-IMPORT"),
        ("06_subprocess_call.py", SafetyDecision.NEEDS_HUMAN_REVIEW, "TSG-PROCESS-SUBPROCESS"),
        ("07_shell_injection.py", SafetyDecision.DENY, "TSG-SHELL-INJECTION"),
        ("08_dependency_install.sh", SafetyDecision.DENY, "TSG-DEPENDENCY-INSTALL"),
        ("09_infinite_loop.py", SafetyDecision.DENY, "TSG-RESOURCE-INFINITE-LOOP"),
        ("10_sensitive_output.py", SafetyDecision.DENY, "TSG-SECRETS-SINK"),
        ("11_bash_pipe.sh", SafetyDecision.NEEDS_HUMAN_REVIEW, "TSG-SHELL-CONTROL"),
        ("12_needs_review.py", SafetyDecision.NEEDS_HUMAN_REVIEW, "TSG-NETWORK-DYNAMIC"),
    ],
)
def test_issue_required_samples(sample, decision, rule_id):
    report = scan_sample(sample)
    assert report.decision == decision
    assert "decision" in report.to_dict()
    if rule_id:
        assert any(finding.rule_id == rule_id for finding in report.findings)
        first = report.findings[0].to_dict()
        assert first["rule_id"]
        assert first["evidence"]
        assert first["recommendation"]
        assert first["risk_level"]


def test_public_samples_emit_structured_reports_and_meet_acceptance_rates():
    high_risk_samples = {
        "02_dangerous_delete.sh",
        "03_read_secret.py",
        "04_network_external.py",
        "07_shell_injection.py",
        "08_dependency_install.sh",
        "09_infinite_loop.py",
        "10_sensitive_output.py",
    }
    safe_samples = {"01_safe_python.py", "05_network_allowlist.py"}
    required_rule_by_sample = {
        "02_dangerous_delete.sh": "TSG-FILE-RECURSIVE-DELETE",
        "03_read_secret.py": "TSG-FILE-DENIED-PATH",
        "04_network_external.py": "TSG-NETWORK-NONALLOWLIST",
    }

    sample_paths = sorted(SAMPLES.iterdir())
    reports = {path.name: scan_sample(path.name) for path in sample_paths}

    assert len(reports) == 12
    for report in reports.values():
        payload = json.loads(report.to_json())
        assert payload["decision"]
        assert payload["risk_level"]
        if payload["decision"] != SafetyDecision.ALLOW.value:
            for finding in payload["findings"]:
                assert finding["rule_id"]
                assert finding["evidence"]
                assert finding["recommendation"]

    detected_high_risk = sum(1 for name in high_risk_samples if reports[name].decision == SafetyDecision.DENY)
    false_positive_safe = sum(1 for name in safe_samples if reports[name].decision != SafetyDecision.ALLOW)
    assert detected_high_risk / len(high_risk_samples) >= 0.9
    assert false_positive_safe / len(safe_samples) <= 0.1

    for sample, rule_id in required_rule_by_sample.items():
        report = reports[sample]
        assert report.decision == SafetyDecision.DENY
        assert any(finding.rule_id == rule_id for finding in report.findings)


def test_cli_scans_public_samples_and_writes_structured_reports(tmp_path, monkeypatch):
    for sample in sorted(SAMPLES.iterdir()):
        report_path = tmp_path / f"{sample.name}.json"
        audit_path = tmp_path / "audit.jsonl"
        language = "bash" if sample.suffix == ".sh" else "python"
        monkeypatch.setattr(
            "sys.argv",
            [
                "tool_safety_check.py",
                str(sample),
                "--language",
                language,
                "--policy",
                str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
                "--report-out",
                str(report_path),
                "--audit-out",
                str(audit_path),
            ],
        )

        exit_code = tool_safety_check_main()

        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert exit_code in {0, 2}
        assert payload["decision"]
        assert payload["risk_level"]
        if payload["decision"] != SafetyDecision.ALLOW.value:
            assert payload["findings"]
            assert payload["findings"][0]["rule_id"]
            assert payload["findings"][0]["evidence"]
            assert payload["findings"][0]["recommendation"]


def test_denied_path_policy_change_changes_result():
    script = 'open(".env", "r").read()'
    strict = ToolSafetyScanner(policy(denied_paths=[".env"]))
    relaxed = ToolSafetyScanner(policy(denied_paths=["~/.ssh"]))

    assert strict.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.DENY
    assert relaxed.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.ALLOW


def test_allowed_domain_policy_change_changes_result():
    script = 'import requests\nrequests.get("https://evil.example.net/collect")'
    strict = ToolSafetyScanner(policy(allowed_domains=["api.github.com"]))
    relaxed = ToolSafetyScanner(policy(allowed_domains=["evil.example.net"]))

    assert strict.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.DENY
    assert relaxed.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.ALLOW


def test_allowed_command_policy_change_changes_result():
    script = "custom_tool --flag"
    strict = ToolSafetyScanner(policy(allowed_commands=["echo"]))
    relaxed = ToolSafetyScanner(policy(allowed_commands=["custom_tool"]))

    strict_report = strict.scan(ToolSafetyScanRequest(script="", language="python", command_args=[script]))
    relaxed_report = relaxed.scan(ToolSafetyScanRequest(script="", language="python", command_args=[script]))
    assert strict_report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert relaxed_report.decision == SafetyDecision.ALLOW


@pytest.mark.parametrize(
    ("script", "language", "rule_id", "decision"),
    [
        ("echo data > /etc/app.conf", "bash", "TSG-FILE-SYSTEM-WRITE", SafetyDecision.DENY),
        ("python worker.py &", "bash", "TSG-PROCESS-BACKGROUND", SafetyDecision.NEEDS_HUMAN_REVIEW),
        ("printf '%s\n' a b | xargs -P 0 -n1 echo", "bash", "TSG-RESOURCE-PARALLELISM", SafetyDecision.DENY),
        ("timeout 999 python job.py", "bash", "TSG-RESOURCE-LONG-TIMEOUT", SafetyDecision.NEEDS_HUMAN_REVIEW),
        (
            "import socket\ns = socket.socket()\ns.connect(('evil.example.net', 443))",
            "python",
            "TSG-NETWORK-NONALLOWLIST",
            SafetyDecision.DENY,
        ),
        (
            "import subprocess\nsubprocess.run(['python', 'job.py'], timeout=999, capture_output=True)",
            "python",
            "TSG-RESOURCE-LONG-TIMEOUT",
            SafetyDecision.NEEDS_HUMAN_REVIEW,
        ),
        (
            "import concurrent.futures\nconcurrent.futures.ThreadPoolExecutor(max_workers=999)",
            "python",
            "TSG-RESOURCE-PARALLELISM",
            SafetyDecision.NEEDS_HUMAN_REVIEW,
        ),
    ],
)
def test_additional_required_risk_coverage(script, language, rule_id, decision):
    report = ToolSafetyScanner(policy()).scan(ToolSafetyScanRequest(script=script, language=language))

    assert report.decision == decision
    assert any(finding.rule_id == rule_id for finding in report.findings)


def test_subprocess_output_capture_has_output_size_recommendation():
    script = "import subprocess\nsubprocess.run(['python', 'job.py'], stdout=subprocess.PIPE)"
    report = ToolSafetyScanner(policy()).scan(ToolSafetyScanRequest(script=script, language="python"))

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    finding = next(item for item in report.findings if item.rule_id == "TSG-RESOURCE-OUTPUT-CAPTURE")
    assert "max_output_bytes" in finding.recommendation


def test_audit_event_contains_required_fields(tmp_path):
    report = ToolSafetyScanner(policy()).scan(
        ToolSafetyScanRequest(script="rm -rf /tmp/demo", language="bash", tool_metadata={"name": "Bash"}))
    event = ToolSafetyAuditLogger(tmp_path / "audit.jsonl").write(report)

    assert event["tool_name"] == "Bash"
    assert event["decision"] == "deny"
    assert event["risk_level"] == "high"
    assert event["rule_id"]
    assert event["duration_ms"] >= 0
    assert event["redacted"] is False
    assert event["blocked"] is True


def test_report_includes_telemetry_attributes():
    report = ToolSafetyScanner(policy()).scan(ToolSafetyScanRequest(script="while True:\n    pass", language="python"))

    assert report.telemetry_attributes["tool.safety.decision"] == "deny"
    assert report.telemetry_attributes["tool.safety.risk_level"] == "high"
    assert report.telemetry_attributes["tool.safety.rule_id"] == "TSG-RESOURCE-INFINITE-LOOP"


@pytest.mark.asyncio
async def test_tool_filter_blocks_before_handle():
    filter_ = ToolSafetyFilter(guard=ToolSafetyGuard(policy=policy()))
    handle = AsyncMock(return_value={"success": True})

    result = await filter_.run(create_agent_context(), {"command": "rm -rf /tmp/demo"}, handle)

    assert result.is_continue is False
    assert result.rsp["error"] == "TOOL_SAFETY_GUARD_BLOCKED"
    handle.assert_not_called()


@pytest.mark.asyncio
async def test_tool_filter_block_writes_audit_event(tmp_path):
    audit_path = tmp_path / "tool_safety_audit.jsonl"
    filter_ = ToolSafetyFilter(guard=ToolSafetyGuard(policy=policy(), audit_log_path=audit_path))
    handle = AsyncMock(return_value={"success": True})

    result = await filter_.run(create_agent_context(), {"command": "rm -rf /tmp/demo"}, handle)

    assert result.is_continue is False
    handle.assert_not_called()
    event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert event["decision"] == "deny"
    assert event["rule_id"] == "TSG-FILE-RECURSIVE-DELETE"
    assert event["blocked"] is True


@pytest.mark.asyncio
async def test_tool_filter_allows_safe_command():
    filter_ = ToolSafetyFilter(guard=ToolSafetyGuard(policy=policy()))
    handle = AsyncMock(return_value={"success": True})

    result = await filter_.run(create_agent_context(), {"command": "echo hello"}, handle)

    assert result.rsp == {"success": True}
    handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_safety_guarded_code_executor_blocks_delegate():
    class DelegateExecutor(BaseCodeExecutor):
        called: bool = False

        async def execute_code(self, invocation_context, code_execution_input):
            self.called = True
            return create_code_execution_result(stdout="executed")

    delegate = DelegateExecutor()
    executor = SafetyGuardedCodeExecutor(delegate=delegate, guard=ToolSafetyGuard(policy=policy()))

    result = await executor.execute_code(
        invocation_context=AsyncMock(),
        code_execution_input=CodeExecutionInput(code_blocks=[CodeBlock(code="while True:\n    pass", language="python")]),
    )

    assert "Tool safety guard blocked" in result.output
    assert delegate.called is False


def test_scans_500_line_script_under_one_second():
    script = "\n".join(f'print("line {i}")' for i in range(500))
    scanner = ToolSafetyScanner(policy())
    start = time.perf_counter()
    report = scanner.scan(ToolSafetyScanRequest(script=script, language="python"))
    elapsed = time.perf_counter() - start

    assert report.decision == SafetyDecision.ALLOW
    assert elapsed < 1.0
