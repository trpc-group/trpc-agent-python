# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for tool safety scanner rules."""

from __future__ import annotations

from pathlib import Path

import yaml

from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyScanner


def _samples():
    path = Path(__file__).with_name("samples.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_samples_match_expected_decisions():
    scanner = SafetyScanner(SafetyPolicy(allowed_domains=["api.example.com"]))
    samples = _samples()

    for sample in samples:
        report = scanner.scan(content=sample["content"], language=sample["language"], tool_name=sample["name"])
        assert report.decision == SafetyDecision(sample["expected_decision"]), sample["name"]
        for rule_id in sample["expected_rule_ids"]:
            assert rule_id in report.rule_ids, sample["name"]
        assert report.elapsed_ms >= 0
        assert report.risk_level.value


def test_secret_delete_and_network_are_denied():
    scanner = SafetyScanner(SafetyPolicy(allowed_domains=["api.example.com"]))

    delete_report = scanner.scan(content="rm -rf /", language="bash")
    secret_report = scanner.scan(content='open("~/.ssh/id_rsa").read()', language="python")
    network_report = scanner.scan(content='curl https://exfil.example/path', language="bash")

    assert delete_report.decision == SafetyDecision.DENY
    assert secret_report.decision == SafetyDecision.DENY
    assert network_report.decision == SafetyDecision.DENY


def test_policy_changes_allowlist_without_code_changes():
    scanner = SafetyScanner(SafetyPolicy(allowed_domains=["allowed.example"]))

    allowed = scanner.scan(content="curl https://allowed.example/status", language="bash")
    denied = scanner.scan(content="curl https://blocked.example/status", language="bash")

    assert allowed.decision == SafetyDecision.ALLOW
    assert denied.decision == SafetyDecision.DENY


def test_policy_can_disable_a_rule():
    policy = SafetyPolicy.model_validate({
        "rules": {
            "DEPENDENCY_INSTALL": {
                "enabled": False
            }
        }
    })
    report = SafetyScanner(policy).scan(content="pip install demo", language="bash")

    assert "DEPENDENCY_INSTALL" in report.rule_ids
    assert report.decision == SafetyDecision.ALLOW


def test_policy_limits_timeout_from_metadata():
    policy = SafetyPolicy(max_timeout_seconds=30)
    report = SafetyScanner(policy).scan(content="echo hello", language="bash", metadata={"timeout": 120})

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "RESOURCE_LONG_SLEEP" in report.rule_ids


def test_sensitive_evidence_is_redacted():
    report = SafetyScanner().scan(content='password = "super-secret-value"\nprint(password)', language="python")

    assert report.redacted is True
    assert any("<redacted:secret>" in finding.evidence for finding in report.findings)


def test_500_line_script_scans_under_one_second():
    content = "\n".join(f"x_{i} = {i}" for i in range(500))
    report = SafetyScanner().scan(content=content, language="python")

    assert report.decision == SafetyDecision.ALLOW
    assert report.elapsed_ms <= 1000
