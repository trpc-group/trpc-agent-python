# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Policy loading and hot-reload tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from trpc_agent_sdk.safety import Decision
from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ScanInput


def test_policy_loads_yaml(policy_path):
    p = PolicyConfig.from_yaml(policy_path)
    assert "api.github.com" in p.whitelisted_domains
    assert ".env" in p.forbidden_paths
    assert p.max_timeout_seconds == 300


def test_policy_from_dict_defaults():
    p = PolicyConfig.from_dict({})
    assert p.whitelisted_domains == []
    assert p.deny_risk_level.name == "HIGH"


def test_hot_reload_changes_whitelist(tmp_path: Path):
    yaml_a = tmp_path / "a.yaml"
    yaml_b = tmp_path / "b.yaml"
    yaml_a.write_text("whitelisted_domains: []\n", encoding="utf-8")
    yaml_b.write_text("whitelisted_domains: [api.github.com]\n", encoding="utf-8")

    script = "import requests\nrequests.get('https://api.github.com')\n"
    inp = ScanInput(script=script, language="python")

    pa = PolicyConfig.from_yaml(yaml_a)
    ra = SafetyScanner(pa).scan(inp)
    assert ra.decision == Decision.DENY

    pb = PolicyConfig.from_yaml(yaml_b)
    rb = SafetyScanner(pb).scan(inp)
    assert rb.decision == Decision.ALLOW


def test_hot_reload_changes_forbidden_path(tmp_path: Path):
    yaml_a = tmp_path / "a.yaml"
    yaml_a.write_text("forbidden_paths: []\n", encoding="utf-8")
    yaml_b = tmp_path / "b.yaml"
    yaml_b.write_text("forbidden_paths: ['/data']\n", encoding="utf-8")

    script = "cat /data/secrets"
    inp = ScanInput(script=script, language="bash")

    ra = SafetyScanner(PolicyConfig.from_yaml(yaml_a)).scan(inp)
    rb = SafetyScanner(PolicyConfig.from_yaml(yaml_b)).scan(inp)
    assert len(rb.findings) >= len(ra.findings)


def test_disabled_rules_skipped(tmp_path: Path):
    yaml = tmp_path / "p.yaml"
    yaml.write_text("disabled_rules: [R003_process_system]\n", encoding="utf-8")
    p = PolicyConfig.from_yaml(yaml)
    scanner = SafetyScanner(p)
    inp = ScanInput(
        script="import subprocess\nsubprocess.run('ls')\n",
        language="python",
    )
    report = scanner.scan(inp)
    assert "R003_process_system" not in report.rule_ids


def test_strict_policy_rejects_unknown_keys():
    with pytest.raises(ValueError, match="unknown policy keys"):
        PolicyConfig.from_dict({"strict_policy": True, "not_a_real_key": 1})


def test_strict_command_allowlist(tmp_path: Path):
    p = PolicyConfig.from_dict({
        "allowed_commands": ["ls", "echo"],
        "strict_command_allowlist": True,
    })
    report = SafetyScanner(p).scan(ScanInput(script="curl https://x", language="bash"))
    assert report.decision == Decision.DENY


def test_block_on_review_flag_in_policy():
    p = PolicyConfig.from_dict({"block_on_review": True})
    assert p.block_on_review is True
