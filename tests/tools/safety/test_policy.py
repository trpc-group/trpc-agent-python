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
    path.write_text(content, encoding="utf-8")
    return SafetyScanner.from_yaml(path)


def test_policy_loads_yaml_and_normalizes_values(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        """
version: "test"
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
    path.write_text("unknown: true\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        SafetyPolicy.from_yaml(path)


def test_policy_rejects_non_positive_limits():
    with pytest.raises(ValidationError):
        SafetyPolicy.model_validate({"limits": {"max_timeout_seconds": 0}})


def test_policy_rejects_blank_allowlist_entry():
    with pytest.raises(ValidationError):
        SafetyPolicy.model_validate({"commands": {"allowed": ["echo", " "]}})


def test_policy_rejects_unknown_rule_override():
    with pytest.raises(ValidationError, match="unknown rule ids"):
        SafetyPolicy.model_validate({"rule_overrides": {"FILE-999": {"enabled": True}}})


def test_allowed_domains_change_network_decision_without_code_change(tmp_path):
    request = SafetyScanRequest(
        content='import requests\nrequests.get("https://service.example.test/data")',
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
    assert allowed.decision == SafetyDecision.ALLOW
