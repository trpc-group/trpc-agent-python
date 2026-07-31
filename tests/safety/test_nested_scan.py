# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Nested propagation, unsupported input, and shared budget tests."""

from trpc_agent_sdk.safety import SafetyDecision
from trpc_agent_sdk.safety import SafetyPolicy
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner


def test_nested_deny_propagates_to_root_and_retains_nested_path(scanner: SafetyScanner):
    report = scanner.scan(SafetyScanRequest(script="bash -c 'rm -rf /'", language="shell"))
    assert report.decision is SafetyDecision.DENY
    finding = next(item for item in report.findings if item.rule_id == "SH.FILESYSTEM.DESTRUCTIVE_DELETE")
    assert finding.nested_path


def test_nested_batch_uses_one_policy_hash(scanner: SafetyScanner):
    report = scanner.scan(
        SafetyScanRequest(
            script="import subprocess\nsubprocess.run(['bash', '-c', 'curl https://bad.invalid'])",
            language="python",
        ))
    assert len(report.policy_hash) == 64
    assert all(item.redacted for item in report.findings)
    assert "SH.NETWORK.NON_WHITELISTED" in report.rule_ids


def test_depth_budget_is_review_blocked():
    policy = SafetyPolicy(schema_version="1", nested={"max_depth": 0})
    report = SafetyScanner(policy).scan(SafetyScanRequest(script="bash -c 'echo x'", language="shell"))
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.execution_blocked
    assert report.failure_code == "nested_budget_exceeded"


def test_total_source_budget_is_review_blocked():
    policy = SafetyPolicy(schema_version="1", nested={"max_total_bytes": 1024})
    report = SafetyScanner(policy).scan(SafetyScanRequest(script="x = 1\n" * 300, language="python"))
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.failure_code == "source_budget_exceeded"


def test_unsupported_language_is_review_not_silent_allow(scanner: SafetyScanner):
    report = scanner.scan(SafetyScanRequest(script="opaque", language="ruby"))
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.execution_blocked
    assert report.failure_code == "unsupported_language"
    assert "CORE.ANALYSIS.UNSUPPORTED_LANGUAGE" in report.rule_ids


def test_dynamic_unknown_is_review_not_silent_allow(scanner: SafetyScanner):
    report = scanner.scan(SafetyScanRequest(script="import requests\nrequests.get(target)", language="python"))
    assert report.decision is SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PY.NETWORK.DYNAMIC_TARGET" in report.rule_ids
    assert report.failure_code == "unknown_static_value"
    assert not report.analysis_complete


def test_unknown_policy_can_raise_dynamic_value_to_deny():
    policy = SafetyPolicy(schema_version="1", failures={"unknown": "deny"})
    report = SafetyScanner(policy).scan(
        SafetyScanRequest(script="import requests\nrequests.get(target)", language="python"))
    assert report.decision is SafetyDecision.DENY


def test_finding_budget_is_review_blocked_and_analysis_incomplete():
    policy = SafetyPolicy(schema_version="1", max_findings=1)
    report = SafetyScanner(policy).scan(
        SafetyScanRequest(
            script="import os\nos.remove('/etc/hosts')\nos.rmdir('/root/cache')",
            language="python",
        ))
    assert report.execution_blocked
    assert report.failure_code == "finding_budget_exceeded"
    assert not report.analysis_complete
    assert report.finding_count == 1
