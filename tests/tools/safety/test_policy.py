# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for policy loading and the "change rules without code" contract."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import default_policy
from trpc_agent_sdk.tools.safety import load_policy


def test_default_policy_loads_and_has_rules() -> None:
    """The bundled default policy loads and exposes a non-empty rule set."""
    policy = default_policy()
    assert policy.rules, "default policy must ship with rules"
    assert policy.allowed_domains, "default policy must define allowed domains"
    assert policy.forbidden_paths, "default policy must define forbidden paths"


def test_default_policy_covers_all_six_categories() -> None:
    """Every risk family required by the issue is represented in the rules."""
    policy = default_policy()
    categories = {rule.category for rule in policy.rules}
    # Six families: file op, network, process, dependency, resource, sensitive.
    assert len(categories) == 6


def test_rule_ids_are_unique_in_default_policy() -> None:
    """Rule ids must be unique so hits map back unambiguously."""
    policy = default_policy()
    ids = [rule.rule_id for rule in policy.rules]
    assert len(ids) == len(set(ids))


def test_load_missing_file_raises() -> None:
    """An explicit, non-existent path fails loudly."""
    with pytest.raises(FileNotFoundError):
        load_policy("does_not_exist_12345.yaml")


def test_duplicate_rule_ids_rejected() -> None:
    """A policy with duplicate rule ids is rejected at validation time."""
    raw = {
        "rules": [
            {"rule_id": "X1", "category": "resource_abuse", "risk_level": "low",
             "title": "a", "pattern": "a"},
            {"rule_id": "X1", "category": "resource_abuse", "risk_level": "low",
             "title": "b", "pattern": "b"},
        ]
    }
    with pytest.raises(Exception):
        SafetyPolicy.model_validate(raw)


def test_invalid_regex_rejected() -> None:
    """A rule whose pattern does not compile is rejected."""
    raw = {
        "rules": [
            {"rule_id": "BAD", "category": "resource_abuse", "risk_level": "low",
             "title": "bad", "pattern": "([unclosed"},
        ]
    }
    with pytest.raises(Exception):
        SafetyPolicy.model_validate(raw)


def test_malformed_yaml_raises_value_error(tmp_path: Path) -> None:
    """Non-mapping YAML content is rejected with a clear error."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(bad)


def test_domain_allow_list_matches_subdomains() -> None:
    """Allow-list matching accepts exact domains and their subdomains."""
    policy = SafetyPolicy(allowed_domains=["openai.com"])
    assert policy.domain_allowed("openai.com")
    assert policy.domain_allowed("api.openai.com")
    assert not policy.domain_allowed("evil.com")
    assert not policy.domain_allowed("notopenai.com")


def test_rules_for_language_filtering() -> None:
    """``rules_for`` returns language-agnostic rules plus language-specific ones."""
    policy = default_policy()
    python_rules = policy.rules_for(ScriptLanguage.PYTHON)
    bash_rules = policy.rules_for(ScriptLanguage.BASH)
    # A bash-only rule (FS001) must not appear for python.
    py_ids = {r.rule_id for r in python_rules}
    bash_ids = {r.rule_id for r in bash_rules}
    assert "FS001" in bash_ids
    assert "FS001" not in py_ids
    # A language-agnostic rule (CR001) appears for both.
    assert "CR001" in py_ids
    assert "CR001" in bash_ids


def test_custom_policy_changes_behaviour_without_code(tmp_path: Path) -> None:
    """A brand-new rule declared purely in YAML takes effect with no code change.

    This is the "策略文件修改后不需要改代码" acceptance criterion: the scanner
    picks up an operator-authored rule solely from the policy file.
    """
    from trpc_agent_sdk.tools.safety import SafetyScanner
    from trpc_agent_sdk.tools.safety import ScanInput
    from trpc_agent_sdk.tools.safety import SafetyDecision

    policy_yaml = textwrap.dedent(
        """
        version: "1"
        allowed_domains: []
        forbidden_paths: []
        redact_sensitive: true
        ast_analysis: false
        rules:
          - rule_id: CUSTOM_MINE
            category: resource_abuse
            risk_level: critical
            title: Forbidden marker token
            pattern: 'FORBIDDEN_MARKER_TOKEN'
        """
    )
    policy_file = tmp_path / "custom.yaml"
    policy_file.write_text(policy_yaml, encoding="utf-8")

    scanner = SafetyScanner(load_policy(policy_file))
    report = scanner.scan(ScanInput(script="echo FORBIDDEN_MARKER_TOKEN",
                                    language=ScriptLanguage.BASH))
    assert report.decision is SafetyDecision.DENY
    assert "CUSTOM_MINE" in report.rule_ids()
    assert report.risk_level is RiskLevel.CRITICAL
