# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel


def test_load_default_policy_has_all_rules(tmp_path):
    policy = load_policy()
    # Every rule_id in the metadata table must resolve to a Rule.
    from trpc_agent_sdk.tools.safety._rules import DEFAULT_RULE_POLICIES
    assert set(policy.rules.keys()) == set(DEFAULT_RULE_POLICIES.keys())
    assert policy.deny_risk_level == RiskLevel.HIGH
    assert policy.review_risk_level == RiskLevel.MEDIUM


def test_rule_overrides_change_decision(tmp_path):
    yaml_text = """
name: t
deny_risk_level: HIGH
review_risk_level: MEDIUM
rule_overrides:
  tool-net-http:
    risk_level: HIGH
    decision: DENY
"""
    p = tmp_path / "p.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    policy = load_policy(p)
    assert policy.rules["tool-net-http"].risk_level == RiskLevel.HIGH
    assert policy.rules["tool-net-http"].decision == Decision.DENY


def test_unknown_field_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("not_a_field: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(p)


def test_bad_enum_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("deny_risk_level: PURPLE\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(p)


def test_negative_number_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("deny_risk_level: -1\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"deny_risk_level must be a string enum name"):
        load_policy(p)


def test_non_string_in_list_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("whitelisted_domains:\n  - example.com\n  - 123\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"whitelisted_domains must contain only strings"):
        load_policy(p)
