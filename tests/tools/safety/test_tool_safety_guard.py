# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

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
