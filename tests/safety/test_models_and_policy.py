# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Models, strict YAML, hashing, reload, and policy modification tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.safety import PolicyLoader
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import SafetyDecision
from trpc_agent_sdk.safety import SafetyPolicy
from trpc_agent_sdk.safety import SafetyRule
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety._policy import _load_policy_text

from .conftest import CANARY


def test_request_raw_fields_are_absent_from_repr_and_default_dump():
    request = SafetyScanRequest(
        script=CANARY,
        language="python",
        env={"TOKEN": CANARY},
        argv=(CANARY, ),
        metadata={"password": CANARY},
    )
    assert CANARY not in repr(request)
    assert CANARY not in str(request.model_dump())


def test_request_bounds_and_extra_are_strict():
    with pytest.raises(ValidationError):
        SafetyScanRequest(script="x", language="python", unexpected=True)
    with pytest.raises(ValidationError):
        SafetyScanRequest(script="x", language="python", metadata={str(index): index for index in range(33)})


def test_policy_requires_schema_and_forbids_extra():
    with pytest.raises(ValidationError):
        SafetyPolicy()
    with pytest.raises(ValidationError):
        SafetyPolicy(schema_version="1", future_option=True)


def test_policy_rejects_duplicate_key_anchor_merge_and_invalid_enum():
    with pytest.raises(Exception, match="duplicate key"):
        _load_policy_text('schema_version: "1"\npolicy_version: a\npolicy_version: b\n')
    with pytest.raises(ValueError, match="anchors and aliases"):
        _load_policy_text('schema_version: "1"\nallowed_commands: &commands [echo]\ndenied_commands: *commands\n')
    with pytest.raises(Exception, match="merge keys|anchors and aliases"):
        _load_policy_text('schema_version: "1"\nbase: &base {enabled: true}\nrules: {X: {<<: *base}}\n')
    with pytest.raises(ValidationError):
        _load_policy_text('schema_version: "1"\ndeny_threshold: extreme\n')


def test_policy_thresholds_review_block_and_failure_allow_are_strict():
    with pytest.raises(ValidationError, match="review_threshold"):
        SafetyPolicy(schema_version="1", review_threshold="critical", deny_threshold="high")
    with pytest.raises(ValidationError):
        SafetyPolicy(schema_version="1", block_on_review=False)
    with pytest.raises(ValidationError, match="cannot be configured to allow"):
        SafetyPolicy(schema_version="1", failures={"parse_failure": "allow"})
    with pytest.raises(ValidationError):
        SafetyPolicy(schema_version="1", redaction={"enabled": False})


def test_policy_is_frozen_normalized_and_hash_is_deterministic():
    first = SafetyPolicy(schema_version="1", allowed_commands=["pwd", "echo", "pwd"])
    second = SafetyPolicy(schema_version="1", allowed_commands=["echo", "pwd"])
    assert first.allowed_commands == ("echo", "pwd")
    assert first.policy_hash == second.policy_hash
    assert len(first.policy_hash) == 64
    with pytest.raises(ValidationError):
        first.policy_version = "changed"
    with pytest.raises(AttributeError):
        first.allowed_commands.append("bad")


def _write_policy(path: Path, version: str, domains: list[str]) -> None:
    path.write_text(
        f'schema_version: "1"\npolicy_version: "{version}"\nwhitelisted_domains:\n' + "".join(f"  - {domain}\n"
                                                                                              for domain in domains),
        encoding="utf-8",
    )


def test_explicit_reload_changes_decision_and_hash_without_code_change(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    _write_policy(path, "a", ["localhost"])
    loader = PolicyLoader(path)
    policy_a = loader.load()
    scanner = SafetyScanner(loader)
    source = "import requests\nrequests.get('https://api.example.com/status')"
    report_a = scanner.scan(SafetyScanRequest(script=source, language="python"))
    _write_policy(path, "b", ["localhost", "api.example.com"])
    policy_b = loader.reload()
    report_b = scanner.scan(SafetyScanRequest(script=source, language="python"))
    assert report_a.decision is SafetyDecision.DENY
    assert report_b.decision is SafetyDecision.ALLOW
    assert policy_a.policy_hash != policy_b.policy_hash
    assert report_a.policy_hash == policy_a.policy_hash
    assert report_b.policy_hash == policy_b.policy_hash


def test_reload_failure_keeps_last_known_good(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    _write_policy(path, "good", ["localhost"])
    loader = PolicyLoader(path)
    original = loader.load()
    path.write_text('schema_version: "1"\nunknown: true\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        loader.reload()
    assert loader.snapshot is original


def test_in_flight_scan_keeps_original_policy_snapshot(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    _write_policy(path, "a", ["localhost"])
    loader = PolicyLoader(path)
    policy_a = loader.load()
    _write_policy(path, "b", ["localhost", "api.example.com"])

    class ReloadDuringRule(SafetyRule):
        rule_id = "TEST.RELOAD_DURING_SCAN"
        languages = ("python", )

        def evaluate(self, context, policy):
            del context
            assert policy is policy_a
            loader.reload()
            return ()

    report = SafetyScanner(loader,
                           rules=[ReloadDuringRule()]).scan(SafetyScanRequest(script="value = 1", language="python"))
    assert report.policy_hash == policy_a.policy_hash
    assert loader.snapshot.policy_version == "b"
    assert loader.snapshot.policy_hash != report.policy_hash


def test_runtime_limits_are_declaration_only():
    policy = SafetyPolicy(
        schema_version="1",
        runtime_limits={
            "enforcement": "declaration_only",
            "memory_mb": 64,
            "max_pids": 4
        },
    )
    assert policy.runtime_limits.enforcement == "declaration_only"
    assert policy.runtime_limits.memory_mb == 64


def test_command_and_forbidden_path_policy_change_behavior():
    command_source = "date"
    default_command = SafetyScanner(SafetyPolicy.default()).scan(
        SafetyScanRequest(script=command_source, language="argv"))
    allowed_command = SafetyScanner(SafetyPolicy(schema_version="1", allowed_commands=["date"])).scan(
        SafetyScanRequest(script=command_source, language="argv"))
    assert default_command.decision is SafetyDecision.NEEDS_HUMAN_REVIEW
    assert allowed_command.decision is SafetyDecision.ALLOW

    protected = SafetyScanner(SafetyPolicy(schema_version="1", forbidden_paths=["/protected"])).scan(
        SafetyScanRequest(script="chmod 600 /protected/file", language="shell"))
    ordinary = SafetyScanner(SafetyPolicy(schema_version="1", forbidden_paths=["/elsewhere"])).scan(
        SafetyScanRequest(script="chmod 600 /protected/file", language="shell"))
    assert protected.decision is SafetyDecision.DENY
    assert ordinary.decision is SafetyDecision.ALLOW


def test_allowed_paths_cannot_override_protected_root_hard_deny():
    policy = SafetyPolicy(schema_version="1", allowed_paths=["/etc"])
    report = SafetyScanner(policy).scan(
        SafetyScanRequest(script="import os\nos.remove('/etc/hosts')", language="python"))
    assert report.decision is SafetyDecision.DENY


def test_zero_findings_allow_with_none_risk(scanner: SafetyScanner):
    report = scanner.scan(SafetyScanRequest(script="value = 1 + 1", language="python"))
    assert report.decision is SafetyDecision.ALLOW
    assert report.risk_level is None
    assert report.findings == ()
    assert report.finding_count == 0


def test_risk_and_decision_are_separate():
    policy = SafetyPolicy(schema_version="1", review_threshold=RiskLevel.HIGH, deny_threshold=RiskLevel.CRITICAL)
    report = SafetyScanner(policy).scan(SafetyScanRequest(script="open('x', 'w')", language="python"))
    assert report.risk_level is RiskLevel.MEDIUM
    assert report.decision is SafetyDecision.ALLOW
