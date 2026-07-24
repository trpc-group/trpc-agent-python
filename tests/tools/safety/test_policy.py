"""Tests for trpc_agent_sdk.tools.safety._policy."""

from __future__ import annotations

import os
import textwrap

import pytest

from trpc_agent_sdk.tools.safety._exceptions import SafetyPolicyError
from trpc_agent_sdk.tools.safety._policy import (
    POLICY_VERSION,
    AuditPolicy,
    CommandsPolicy,
    DefaultsPolicy,
    DependenciesPolicy,
    LimitsPolicy,
    NetworkPolicy,
    PathsPolicy,
    ToolFieldMapping,
    ToolSafetyPolicy,
    _normalize_path_glob,
    is_sensitive_env_key,
    load_safety_policy,
    load_safety_policy_dict,
    match_domain,
    match_path_glob,
    normalize_relpath,
    normalize_script_path_for_match,
)


class TestNetworkPolicy:

    def test_defaults(self):
        net = NetworkPolicy()
        assert net.allow_domains == ()
        assert net.deny_ip_literals is True

    def test_normalize_lowercases(self):
        net = NetworkPolicy(allow_domains=["API.Example.COM"])
        assert net.allow_domains == ("api.example.com", )

    def test_strips_trailing_dot(self):
        net = NetworkPolicy(allow_domains=["api.example.com."])
        assert net.allow_domains == ("api.example.com", )

    def test_none_returns_empty_tuple(self):
        net = NetworkPolicy(allow_domains=None)
        assert net.allow_domains == ()

    def test_string_raises(self):
        with pytest.raises(SafetyPolicyError):
            NetworkPolicy(allow_domains="api.example.com")

    def test_wildcard_must_start_with_star_dot(self):
        with pytest.raises(SafetyPolicyError):
            NetworkPolicy(allow_domains=["example.*"])

    def test_wildcard_prefix_ok(self):
        net = NetworkPolicy(allow_domains=["*.example.com"])
        assert net.allow_domains == ("*.example.com", )

    def test_empty_entries_rejected(self):
        with pytest.raises(SafetyPolicyError):
            NetworkPolicy(allow_domains=["  "])

    def test_non_string_entry_rejected(self):
        with pytest.raises(SafetyPolicyError):
            NetworkPolicy(allow_domains=[123])  # type: ignore[list-item]


class TestCommandsPolicy:

    def test_defaults(self):
        cmds = CommandsPolicy()
        assert cmds.allow == ()
        assert cmds.deny == ()

    def test_normalize_lowercases(self):
        cmds = CommandsPolicy(allow=["LS"], deny=["RM"])
        assert cmds.allow == ("ls", )
        assert cmds.deny == ("rm", )

    def test_string_rejected(self):
        with pytest.raises(SafetyPolicyError):
            CommandsPolicy(allow="ls")


class TestPathsPolicy:

    def test_defaults(self):
        p = PathsPolicy()
        assert p.deny == ()

    def test_normalizes_paths(self):
        p = PathsPolicy(deny=["/etc///passwd", "/var/log/../log/app"])
        assert all("\\" not in d for d in p.deny)

    def test_string_rejected(self):
        with pytest.raises(SafetyPolicyError):
            PathsPolicy(deny="/etc")


class TestLimitsPolicy:

    def test_defaults(self):
        lim = LimitsPolicy()
        assert lim.max_timeout_seconds == 60.0
        assert lim.max_output_bytes == 1_048_576
        assert lim.max_parallel_tasks == 16

    def test_negative_rejected(self):
        with pytest.raises(SafetyPolicyError):
            LimitsPolicy(max_timeout_seconds=-1)

    def test_negative_any_field_rejected(self):
        with pytest.raises(SafetyPolicyError):
            LimitsPolicy(max_processes=-2)

    def test_zero_ok(self):
        lim = LimitsPolicy(max_processes=0, max_parallel_tasks=0)
        assert lim.max_processes == 0


class TestDefaultsPolicy:

    def test_defaults(self):
        d = DefaultsPolicy()
        assert d.unknown_construct == "needs_human_review"
        assert d.guard_error == "deny"
        assert d.human_review_blocks_execution is True

    def test_invalid_value(self):
        with pytest.raises(SafetyPolicyError):
            DefaultsPolicy(unknown_construct="bogus")

    def test_invalid_guard_error(self):
        with pytest.raises(SafetyPolicyError):
            DefaultsPolicy(guard_error="maybe")


class TestDependenciesPolicy:

    def test_default_is_deny(self):
        assert DependenciesPolicy().decision == "deny"


class TestToolFieldMapping:

    def test_defaults(self):
        m = ToolFieldMapping()
        assert m.execution_capable is False
        assert m.language.value == "unknown"


class TestToolSafetyPolicy:

    def test_default_construct(self):
        p = ToolSafetyPolicy()
        assert p.version == POLICY_VERSION
        assert isinstance(p.network, NetworkPolicy)
        assert isinstance(p.audit, AuditPolicy)

    def test_unsupported_version(self):
        with pytest.raises(SafetyPolicyError):
            ToolSafetyPolicy(version="99")

    def test_rule_overrides_valid(self):
        p = ToolSafetyPolicy(rule_overrides={"FILE001_RECURSIVE_DELETE": "allow"})
        assert p.rule_overrides["FILE001_RECURSIVE_DELETE"] == "allow"

    def test_rule_overrides_invalid(self):
        with pytest.raises(SafetyPolicyError):
            ToolSafetyPolicy(rule_overrides={"X": "maybe"})

    def test_hash_is_stable(self):
        a = ToolSafetyPolicy()
        b = ToolSafetyPolicy()
        assert a.hash == b.hash

    def test_hash_changes_with_content(self):
        a = ToolSafetyPolicy()
        b = ToolSafetyPolicy(network=NetworkPolicy(allow_domains=["api.example.com"]))
        assert a.hash != b.hash

    def test_sensitive_env_key_patterns_default(self):
        p = ToolSafetyPolicy()
        assert any("KEY" in pat for pat in p.sensitive_env_key_patterns)

    def test_extra_field_rejected(self):
        with pytest.raises(Exception):
            ToolSafetyPolicy(bogus=1)  # type: ignore[call-arg]


class TestLoadSafetyPolicyDict:

    def test_minimal_dict(self):
        p = load_safety_policy_dict({"version": POLICY_VERSION})
        assert isinstance(p, ToolSafetyPolicy)

    def test_missing_version_added(self):
        p = load_safety_policy_dict({})
        assert p.version == POLICY_VERSION

    def test_invalid_value_raises(self):
        with pytest.raises(SafetyPolicyError):
            load_safety_policy_dict({"network": {"allow_domains": "x"}})


class TestLoadSafetyPolicyFile:

    def test_missing_file(self, tmp_path):
        with pytest.raises(SafetyPolicyError):
            load_safety_policy(tmp_path / "nope.yaml")

    def test_invalid_yaml(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text(":\n  - [unterminated", encoding="utf-8")
        with pytest.raises(SafetyPolicyError):
            load_safety_policy(path)

    def test_empty_file(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("", encoding="utf-8")
        with pytest.raises(SafetyPolicyError):
            load_safety_policy(path)

    def test_non_mapping_root(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("- just\n- a list\n", encoding="utf-8")
        with pytest.raises(SafetyPolicyError):
            load_safety_policy(path)

    def test_full_yaml_round_trip(self, tmp_path):
        body = textwrap.dedent("""
            version: "1"
            network:
              allow_domains:
                - api.example.com
            commands:
              deny:
                - rm
            paths:
              deny:
                - /etc/**
            limits:
              max_timeout_seconds: 30.0
            defaults:
              unknown_construct: deny
            audit:
              enabled: false
              required: false
        """)
        path = tmp_path / "p.yaml"
        path.write_text(body, encoding="utf-8")
        p = load_safety_policy(path)
        assert "api.example.com" in p.network.allow_domains
        assert "rm" in p.commands.deny
        assert p.limits.max_timeout_seconds == 30.0


class TestPathGlobMatching:

    def test_normalize_empty(self):
        assert _normalize_path_glob("") == ""

    def test_normalize_collapse_slashes(self):
        assert _normalize_path_glob("/etc//x") == "/etc/x"

    def test_normalize_strip_relative(self):
        assert _normalize_path_glob("./etc/./x") == "etc/x"

    def test_normalize_tilde(self):
        assert _normalize_path_glob("~/.ssh/x") == "~/.ssh/x"

    def test_normalize_parent_ref_removed(self):
        # '..' is filtered out, so we don't traverse up.
        assert ".." not in _normalize_path_glob("/a/../b")

    def test_normalize_root_only(self):
        # The normalizer filters empty segments; "/" has nothing left, so
        # it collapses to the default "." sentinel.
        assert _normalize_path_glob("/") == "."

    def test_normalize_only_dot(self):
        assert _normalize_path_glob(".") == "."

    def test_normalize_relpath_passthrough(self):
        assert normalize_relpath("/etc/x") == "/etc/x"

    def test_match_exact(self):
        assert match_path_glob("/etc/passwd", "/etc/passwd") is True

    def test_match_prefix_dir(self):
        # Pattern matches when path lives inside the pattern directory.
        assert match_path_glob("/etc/passwd", "/etc") is True

    def test_match_glob_double_star(self):
        assert match_path_glob("/etc/foo/bar", "/etc/**") is True

    def test_match_glob_single_star(self):
        assert match_path_glob("/etc/passwd", "/etc/*") is True

    def test_match_no_match(self):
        assert match_path_glob("/var/log/x", "/etc/**") is False

    def test_match_empty_inputs(self):
        assert match_path_glob("", "/etc") is False
        assert match_path_glob("/etc", "") is False

    def test_match_relative_with_tilde(self):
        # Absolute path matching against an absolute tilde pattern works.
        assert match_path_glob("~/.ssh/config", "~/.ssh/**") is True

    def test_normalize_script_path_for_match_expanduser(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/me")
        out = normalize_script_path_for_match("~/secrets")
        assert "secrets" in out

    def test_normalize_script_path_empty(self):
        assert normalize_script_path_for_match("") == ""


class TestMatchDomain:

    def test_exact_match(self):
        assert match_domain("api.example.com", ["api.example.com"]) is True

    def test_case_insensitive(self):
        assert match_domain("API.Example.COM", ["api.example.com"]) is True

    def test_trailing_dot_stripped(self):
        assert match_domain("api.example.com.", ["api.example.com"]) is True

    def test_wildcard_one_level(self):
        assert match_domain("api.example.com", ["*.example.com"]) is True

    def test_wildcard_does_not_match_apex(self):
        assert match_domain("example.com", ["*.example.com"]) is False

    def test_wildcard_does_not_match_two_levels(self):
        assert match_domain("a.b.example.com", ["*.example.com"]) is False

    def test_empty_host(self):
        assert match_domain("", ["api.example.com"]) is False


class TestSensitiveEnvKey:

    def test_default_patterns_match(self):
        patterns = ("*KEY*", "*TOKEN*", "*PASSWORD*", "*SECRET*", "*CREDENTIAL*")
        assert is_sensitive_env_key("API_KEY", patterns) is True
        assert is_sensitive_env_key("AUTH_TOKEN", patterns) is True
        assert is_sensitive_env_key("DB_PASSWORD", patterns) is True
        assert is_sensitive_env_key("USER_SECRET", patterns) is True
        assert is_sensitive_env_key("AWS_CREDENTIAL", patterns) is True

    def test_non_sensitive(self):
        assert is_sensitive_env_key("PATH", ("*KEY*", "*TOKEN*")) is False

    def test_empty(self):
        assert is_sensitive_env_key("", ("*KEY*", )) is False
