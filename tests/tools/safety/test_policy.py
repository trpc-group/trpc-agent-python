# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for policy loading, validation and hot-reload (acceptance 6)."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety.models import Decision
from trpc_agent_sdk.tools.safety.models import Language
from trpc_agent_sdk.tools.safety.models import ScanInput
from trpc_agent_sdk.tools.safety.engine import SafetyEngine
from trpc_agent_sdk.tools.safety.policy import ENV_POLICY_PATH
from trpc_agent_sdk.tools.safety.policy import PolicyError
from trpc_agent_sdk.tools.safety.policy import SafetyPolicy
from trpc_agent_sdk.tools.safety.policy import load_policy

_ALLOWLISTED_REQUEST = 'import requests\nrequests.get("https://api.trusted.example/v1")'


class TestPolicyDefaults:

    def test_default_has_empty_egress_allowlist(self):
        policy = SafetyPolicy.default()
        assert policy.allow_domains == []
        assert policy.is_domain_allowed("api.trusted.example") is False

    def test_subdomain_match(self):
        policy = SafetyPolicy(allow_domains=["example.com"])
        assert policy.is_domain_allowed("api.example.com") is True
        assert policy.is_domain_allowed("example.com") is True
        assert policy.is_domain_allowed("evil.com") is False

    def test_load_without_path_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_POLICY_PATH, raising=False)
        policy = load_policy()
        assert isinstance(policy, SafetyPolicy)
        assert policy.allow_domains == []


class TestPolicyLoadingFailFast:

    def test_missing_explicit_file_raises(self):
        with pytest.raises(PolicyError):
            load_policy("/nonexistent/path/policy.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("allow_domains: [unterminated\n", encoding="utf-8")
        with pytest.raises(PolicyError):
            load_policy(str(bad))

    def test_empty_file_raises(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(PolicyError):
            load_policy(str(empty))

    def test_bad_redact_regex_raises(self, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("redact:\n  patterns:\n    - '([unclosed'\n", encoding="utf-8")
        with pytest.raises(PolicyError):
            load_policy(str(f))

    def test_invalid_threshold_value_raises(self, tmp_path):
        f = tmp_path / "p.yaml"
        f.write_text("decision_thresholds:\n  critical: explode\n", encoding="utf-8")
        with pytest.raises(PolicyError):
            load_policy(str(f))

    def test_env_var_path_is_honoured(self, tmp_path, monkeypatch):
        f = tmp_path / "p.yaml"
        f.write_text("allow_domains: [env.example]\n", encoding="utf-8")
        monkeypatch.setenv(ENV_POLICY_PATH, str(f))
        policy = load_policy()
        assert policy.is_domain_allowed("env.example") is True


class TestPolicyHotReload:
    """Changing only the YAML changes the decision -- no code change (acceptance 6)."""

    def _write(self, tmp_path, name, body):
        f = tmp_path / name
        f.write_text(body, encoding="utf-8")
        return str(f)

    def test_allowlist_toggles_decision(self, tmp_path):
        permissive = self._write(tmp_path, "allow.yaml", "allow_domains:\n  - api.trusted.example\n")
        strict = self._write(tmp_path, "strict.yaml", "allow_domains: []\n")

        permissive_engine = SafetyEngine(load_policy(permissive))
        strict_engine = SafetyEngine(load_policy(strict))

        scan_input = ScanInput(script=_ALLOWLISTED_REQUEST, language=Language.PYTHON, tool_name="t")
        assert permissive_engine.scan(scan_input).decision == Decision.ALLOW
        assert strict_engine.scan(scan_input).decision == Decision.DENY

    def test_threshold_retuning_changes_decision(self, tmp_path):
        # Tighten MEDIUM from review to deny: a subprocess call then becomes deny.
        # (Thresholds escalate severity; see design section 4 -- the decision is
        # the more severe of the rule action and the level threshold.)
        strict = self._write(tmp_path, "strict_threshold.yaml", "decision_thresholds:\n  medium: deny\n")
        engine = SafetyEngine(load_policy(strict))
        report = engine.scan(ScanInput(script='import subprocess\nsubprocess.run(["ls"])',
                                       language=Language.PYTHON, tool_name="t"))
        assert report.decision == Decision.DENY
