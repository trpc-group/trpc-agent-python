"""Tests for policy loading, normalization, and hashing."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety._exceptions import SafetyPolicyError
from trpc_agent_sdk.tools.safety._policy import (
    POLICY_VERSION,
    load_safety_policy,
    load_safety_policy_dict,
    match_domain,
    match_path_glob,
)


def write_policy(tmp_path: Path, data: dict) -> str:
    import yaml as _yaml

    path = tmp_path / "policy.yaml"
    path.write_text(_yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def test_load_example_policy(example_policy_path):
    policy = load_safety_policy(example_policy_path)
    assert policy.version == POLICY_VERSION
    assert policy.hash
    assert "api.github.com" in policy.network.allow_domains


def test_load_unknown_field_fails(tmp_path):
    path = write_policy(tmp_path, {"version": "1", "unknown_field": True})
    with pytest.raises(SafetyPolicyError):
        load_safety_policy(path)


def test_load_invalid_version_fails(tmp_path):
    path = write_policy(tmp_path, {"version": "99"})
    with pytest.raises(SafetyPolicyError):
        load_safety_policy(path)


def test_negative_limit_rejected(strict_policy_dict):
    bad = copy.deepcopy(strict_policy_dict)
    bad["limits"] = {"max_timeout_seconds": -1}
    with pytest.raises(SafetyPolicyError):
        load_safety_policy_dict(bad)


def test_invalid_rule_override_rejected(strict_policy_dict):
    bad = copy.deepcopy(strict_policy_dict)
    bad["rule_overrides"] = {"FILE001_RECURSIVE_DELETE": "delete-please"}
    with pytest.raises(SafetyPolicyError):
        load_safety_policy_dict(bad)


def test_invalid_decision_default_rejected(strict_policy_dict):
    bad = copy.deepcopy(strict_policy_dict)
    bad["defaults"] = {"unknown_construct": "permit"}
    with pytest.raises(SafetyPolicyError):
        load_safety_policy_dict(bad)


def test_wildcard_domain_must_start_with_star(strict_policy_dict):
    bad = copy.deepcopy(strict_policy_dict)
    bad["network"] = {"allow_domains": ["foo.*.example.com"]}
    with pytest.raises(SafetyPolicyError):
        load_safety_policy_dict(bad)


def test_match_domain_exact_and_wildcard():
    allowed = ("api.github.com", "*.internal.example.com")
    assert match_domain("api.github.com", allowed)
    assert match_domain("API.GITHUB.COM", allowed)
    assert match_domain("service.internal.example.com", allowed)
    assert not match_domain("internal.example.com", allowed)
    assert not match_domain("a.b.internal.example.com", allowed)
    assert not match_domain("evil.example.com", allowed)


def test_match_path_glob_lexical():
    assert match_path_glob("~/.ssh/id_rsa", "~/.ssh")
    assert match_path_glob("/etc/passwd", "/etc")
    assert match_path_glob(".env", ".env")
    assert not match_path_glob("/home/user/code", "/etc")


def test_policy_hash_stable_across_formatting(tmp_path, strict_policy_dict):
    import yaml as _yaml

    p1 = tmp_path / "a.yaml"
    p2 = tmp_path / "b.yaml"
    p1.write_text(_yaml.safe_dump(strict_policy_dict), encoding="utf-8")
    p2.write_text(_yaml.safe_dump(strict_policy_dict, sort_keys=False),
                  encoding="utf-8")
    a = load_safety_policy(p1)
    b = load_safety_policy(p2)
    assert a.hash == b.hash


def test_policy_hash_changes_when_lists_change(strict_policy_dict):
    p1 = load_safety_policy_dict(strict_policy_dict)
    modified = copy.deepcopy(strict_policy_dict)
    modified["commands"]["allow"] = list(modified["commands"]["allow"]) + ["go"]
    p2 = load_safety_policy_dict(modified)
    assert p1.hash != p2.hash


def test_sensitive_env_key_patterns_default():
    policy = load_safety_policy_dict({})
    assert policy.sensitive_env_key_patterns
    assert any("KEY" in p for p in policy.sensitive_env_key_patterns)
