# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy loading and validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScriptLanguage


def _scanner_from_yaml(tmp_path, content: str) -> SafetyScanner:
    path = tmp_path / "behavior-policy.yaml"
    header = """
api_version: trpc-agent.io/tool-safety/v1
kind: ToolSafetyPolicy
version: "test"
policy_id: test-policy
"""
    path.write_text(header + content, encoding="utf-8")
    return SafetyScanner.from_yaml(path)


def test_policy_loads_yaml_and_normalizes_values(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        """
api_version: trpc-agent.io/tool-safety/v1
kind: ToolSafetyPolicy
version: "test"
policy_id: test-policy
network:
  allowed_domains: ["API.EXAMPLE.COM.", "api.example.com"]
  allow_subdomains: true
commands:
  allowed: ["echo", "echo"]
paths:
  denied: [".env", "~/.ssh"]
  workspace_only_delete: true
limits:
  max_script_size_bytes: 2048
  max_script_lines: 100
  max_timeout_seconds: 10
  max_output_size_bytes: 1024
  max_concurrency: 4
  max_sleep_seconds: 5
  max_static_write_size_bytes: 4096
rule_overrides:
  DEP-001:
    action: deny
""",
        encoding="utf-8",
    )

    policy = SafetyPolicy.from_yaml(path)

    assert policy.version == "test"
    assert policy.network.allowed_domains == ["api.example.com"]
    assert policy.commands.allowed == ["echo"]
    assert policy.rule_overrides["DEP-001"].action == SafetyDecision.DENY


def test_policy_rejects_unknown_fields(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        """
api_version: trpc-agent.io/tool-safety/v1
kind: ToolSafetyPolicy
unknown: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        SafetyPolicy.from_yaml(path)


def test_policy_rejects_non_positive_limits():
    with pytest.raises(ValidationError):
        SafetyPolicy.model_validate({"limits": {"max_timeout_seconds": 0}})


def test_policy_rejects_blank_allowlist_entry():
    with pytest.raises(ValidationError):
        SafetyPolicy.model_validate({"commands": {"allowed": ["echo", " "]}})


def test_policy_rejects_relative_executable_path():
    with pytest.raises(ValueError, match="must be absolute"):
        SafetyPolicy.model_validate({"commands": {"allowed": ["./echo"]}})


def test_policy_rejects_unknown_rule_override():
    with pytest.raises(ValidationError, match="unknown rule ids"):
        SafetyPolicy.model_validate({"rule_overrides": {"FILE-999": {"enabled": True}}})


@pytest.mark.parametrize(
    "content",
    [
        "kind: ToolSafetyPolicy\n",
        "api_version: trpc-agent.io/tool-safety/v2\nkind: ToolSafetyPolicy\n",
        "api_version: trpc-agent.io/tool-safety/v1\nkind: OtherPolicy\n",
    ],
)
def test_policy_rejects_missing_or_unsupported_schema_contract(tmp_path, content):
    path = tmp_path / "invalid-policy.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises((ValueError, ValidationError)):
        SafetyPolicy.from_yaml(path)


@pytest.mark.parametrize(
    "domain",
    [
        "https://api.example.com",
        "user@api.example.com",
        "*.example.com",
        "example.com/path",
        "bad_domain.example",
    ],
)
def test_policy_rejects_non_hostname_allowlist_entries(domain):
    with pytest.raises(ValidationError):
        SafetyPolicy.model_validate({"network": {"allowed_domains": [domain]}})


def test_policy_accepts_ipv6_literal_allowlist_entry():
    policy = SafetyPolicy.model_validate({"network": {"allowed_domains": ["::1"]}})

    assert policy.network.allowed_domains == ["::1"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_policy_rejects_non_finite_limits(value):
    with pytest.raises(ValidationError):
        SafetyPolicy.model_validate({"limits": {"max_timeout_seconds": value}})


def test_allowed_domains_change_network_decision_without_code_change(tmp_path):
    request = SafetyScanRequest(
        content=("import requests\n"
                 'requests.get("https://service.example.test/data", '
                 "allow_redirects=False)"),
        language=ScriptLanguage.PYTHON,
    )
    denied = _scanner_from_yaml(tmp_path, "network:\n  allowed_domains: []\n").scan(request)
    allowed = _scanner_from_yaml(
        tmp_path,
        'network:\n  allowed_domains: ["service.example.test"]\n',
    ).scan(request)

    assert denied.decision == SafetyDecision.DENY
    assert allowed.decision == SafetyDecision.ALLOW


def test_denied_paths_change_file_decision_without_code_change(tmp_path):
    request = SafetyScanRequest(
        content='open("/srv/private/credentials.txt").read()',
        language=ScriptLanguage.PYTHON,
    )
    allowed = _scanner_from_yaml(tmp_path, "paths:\n  denied: []\n").scan(request)
    denied = _scanner_from_yaml(
        tmp_path,
        'paths:\n  denied: ["/srv/private"]\n',
    ).scan(request)

    assert allowed.decision == SafetyDecision.ALLOW
    assert denied.decision == SafetyDecision.DENY


def test_allowed_commands_change_process_decision_without_code_change(tmp_path):
    request = SafetyScanRequest(
        content="date",
        language=ScriptLanguage.BASH,
    )
    denied = _scanner_from_yaml(tmp_path, "commands:\n  allowed: []\n").scan(request)
    allowed = _scanner_from_yaml(
        tmp_path,
        'commands:\n  allowed: ["date"]\n',
    ).scan(request)

    assert denied.decision == SafetyDecision.DENY
    assert allowed.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert allowed.rule_id == "PROC-UNKNOWN-001"


@pytest.mark.parametrize(
    "content",
    [
        "curl https://api.example.com/data",
        "rm ./workspace-file",
        "dd if=/dev/null of=output.bin count=1",
        "truncate -s 0 output.bin",
    ],
)
def test_profiled_commands_still_require_executable_allowlist(tmp_path, content):
    report = _scanner_from_yaml(
        tmp_path,
        ('network:\n'
         '  allowed_domains: ["api.example.com"]\n'
         "commands:\n"
         "  allowed: []\n"),
    ).scan(SafetyScanRequest(
        content=content,
        language=ScriptLanguage.BASH,
        cwd="/tmp/tool-safety-workspace",
    ))

    assert report.decision == SafetyDecision.DENY
    assert "PROC-002" in {finding.rule_id for finding in report.findings}


def test_limits_and_rule_action_change_behavior_without_code_change(tmp_path):
    timeout_request = SafetyScanRequest(
        content='print("ok")',
        language=ScriptLanguage.PYTHON,
        timeout_seconds=20,
    )
    relaxed_timeout = _scanner_from_yaml(
        tmp_path,
        "limits:\n  max_timeout_seconds: 30\n",
    ).scan(timeout_request)
    strict_timeout = _scanner_from_yaml(
        tmp_path,
        "limits:\n  max_timeout_seconds: 10\n",
    ).scan(timeout_request)
    dependency_request = SafetyScanRequest(
        content='import subprocess\nsubprocess.run(["pip", "install", "demo"])',
        language=ScriptLanguage.PYTHON,
    )
    reviewed = _scanner_from_yaml(tmp_path, "").scan(dependency_request)
    allowed = _scanner_from_yaml(
        tmp_path,
        "rule_overrides:\n  DEP-001:\n    action: allow\n",
    ).scan(dependency_request)

    assert relaxed_timeout.decision == SafetyDecision.ALLOW
    assert strict_timeout.decision == SafetyDecision.DENY
    assert reviewed.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert allowed.decision == SafetyDecision.ALLOW
    assert allowed.policy_relaxed is True


def test_lowering_deny_rule_to_review_is_reported_as_policy_relaxation(tmp_path):
    report = _scanner_from_yaml(
        tmp_path,
        "commands:\n"
        "  allowed: [rm]\n"
        "rule_overrides:\n"
        "  FILE-001:\n"
        "    action: needs_human_review\n",
    ).scan(SafetyScanRequest(
        content="rm -rf /",
        language=ScriptLanguage.BASH,
    ))

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.policy_relaxed is True


def test_unmatched_relaxed_override_does_not_mark_report_as_relaxed(tmp_path):
    report = _scanner_from_yaml(
        tmp_path,
        "rule_overrides:\n"
        "  FILE-001:\n"
        "    action: needs_human_review\n",
    ).scan(SafetyScanRequest(
        content="echo hello",
        language=ScriptLanguage.BASH,
    ))

    assert report.decision == SafetyDecision.ALLOW
    assert report.policy_relaxed is False
