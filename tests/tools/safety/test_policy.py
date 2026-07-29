# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for PolicyConfig loading and query methods."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety import PolicyConfig


class TestPolicyConfigDefault:

    def test_returns_policy_config(self):
        assert isinstance(PolicyConfig.default(), PolicyConfig)

    def test_allowed_commands(self):
        cfg = PolicyConfig.default()
        assert "python" in cfg.allowed_commands
        assert "pytest" in cfg.allowed_commands

    def test_denied_commands(self):
        cfg = PolicyConfig.default()
        assert "rm -rf /" in cfg.denied_commands
        assert "sudo" in cfg.denied_commands

    def test_network_allowlist(self):
        cfg = PolicyConfig.default()
        assert "github.com" in cfg.network_allowlist

    def test_denied_paths(self):
        assert "/etc" in PolicyConfig.default().denied_paths

    def test_resource_limits(self):
        cfg = PolicyConfig.default()
        assert cfg.max_timeout_seconds == 300
        assert cfg.max_output_bytes == 10 * 1024 * 1024
        assert cfg.max_file_write_bytes == 50 * 1024 * 1024

    def test_secret_patterns(self):
        assert len(PolicyConfig.default().secret_patterns) >= 3


class TestPolicyConfigValidate:

    def test_rejects_negative_timeout(self):
        with pytest.raises(ValueError, match="max_timeout_seconds"):
            PolicyConfig.from_dict({"max_timeout_seconds": -1})

    def test_rejects_zero_timeout(self):
        with pytest.raises(ValueError, match="max_timeout_seconds"):
            PolicyConfig.from_dict({"max_timeout_seconds": 0})

    def test_rejects_negative_output_bytes(self):
        with pytest.raises(ValueError, match="max_output_bytes"):
            PolicyConfig.from_dict({"max_output_bytes": -100})

    def test_rejects_negative_file_write_bytes(self):
        with pytest.raises(ValueError, match="max_file_write_bytes"):
            PolicyConfig.from_dict({"max_file_write_bytes": -1})

    def test_rejects_bool_as_int_field(self):
        with pytest.raises(ValueError, match="max_timeout_seconds"):
            PolicyConfig.from_dict({"max_timeout_seconds": True})

    def test_rejects_non_list_for_list_field(self):
        with pytest.raises(ValueError, match="allowed_commands"):
            PolicyConfig.from_dict({"allowed_commands": "python"})

    def test_rejects_list_with_non_string_items(self):
        with pytest.raises(ValueError, match="allowed_commands"):
            PolicyConfig.from_dict({"allowed_commands": ["ok", 123]})

    def test_rejects_non_bool_for_bool_field(self):
        with pytest.raises(ValueError, match="review_shell_pipelines"):
            PolicyConfig.from_dict({"review_shell_pipelines": "yes"})

    def test_allows_valid_values(self):
        cfg = PolicyConfig.from_dict({"max_timeout_seconds": 60})
        assert cfg.max_timeout_seconds == 60

    def test_skips_unknown_keys(self):
        cfg = PolicyConfig.from_dict({"max_timeout_seconds": 10, "unknown": [1, 2, 3]})
        assert cfg.max_timeout_seconds == 10


class TestPolicyConfigFromDict:

    def test_partial_override(self):
        cfg = PolicyConfig.from_dict({"max_timeout_seconds": 60})
        assert cfg.max_timeout_seconds == 60
        assert cfg.max_output_bytes == 10 * 1024 * 1024  # default preserved

    def test_empty(self):
        assert isinstance(PolicyConfig.from_dict({}), PolicyConfig)

    def test_unknown_keys_ignored(self):
        assert isinstance(PolicyConfig.from_dict({"unknown_key": "value"}), PolicyConfig)


class TestPolicyConfigFromYaml:

    def test_valid_yaml(self):
        yaml_content = """
max_timeout_seconds: 45
network_allowlist:
  - example.com
  - api.example.com
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = Path(f.name)
        try:
            cfg = PolicyConfig.from_yaml(tmp_path)
            assert cfg.max_timeout_seconds == 45
            assert "example.com" in cfg.network_allowlist
        finally:
            tmp_path.unlink()

    def test_empty_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            tmp_path = Path(f.name)
        try:
            assert isinstance(PolicyConfig.from_yaml(tmp_path), PolicyConfig)
        finally:
            tmp_path.unlink()

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            PolicyConfig.from_yaml("/nonexistent/path.yaml")

    def test_invalid_yaml_raises_value_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(": invalid : [")
            tmp_path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="Invalid YAML"):
                PolicyConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink()

    def test_non_mapping_raises_value_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("- item1\n- item2\n")
            tmp_path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="mapping"):
                PolicyConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink()


class TestPolicyConfigQueryMethods:

    @pytest.fixture
    def cfg(self):
        return PolicyConfig(
            allowed_commands=["python", "ls"],
            denied_paths=["/etc", "/root"],
            network_allowlist=["github.com", "pypi.org"],
        )

    def test_is_command_allowed_true(self, cfg):
        assert cfg.is_command_allowed("python") is True

    def test_is_command_allowed_false(self, cfg):
        assert cfg.is_command_allowed("rm") is False

    def test_is_path_denied_true(self, cfg):
        assert cfg.is_path_denied("/etc/passwd") is True
        assert cfg.is_path_denied("/root/.bashrc") is True

    def test_is_path_denied_false(self, cfg):
        assert cfg.is_path_denied("/home/user/file.txt") is False

    def test_is_path_denied_exact_match_denied(self, cfg):
        """cwd equal to a denied directory is denied."""
        assert cfg.is_path_denied("/root") is True
        assert cfg.is_path_denied("/etc") is True

    def test_is_path_denied_credential_dir_exact_match_denied(self):
        cfg = PolicyConfig(denied_paths=["~/.ssh"])
        assert cfg.is_path_denied("~/.ssh") is True

    def test_is_path_denied_supports_wildcards(self):
        cfg = PolicyConfig(denied_paths=["*.pem", "*/.env"])
        assert cfg.is_path_denied("certs/client.pem") is True
        assert cfg.is_path_denied("nested/.env") is True
        assert cfg.is_path_denied("workspace/.env.sample") is False

    def test_is_path_denied_sub_path_is_denied(self, cfg):
        """cwd="/root/.ssh" should be denied (sub-path of /root)."""
        assert cfg.is_path_denied("/root/.ssh") is True
        assert cfg.is_path_denied("/etc/passwd") is True

    def test_is_domain_allowed_true(self, cfg):
        assert cfg.is_domain_allowed("github.com") is True

    def test_is_domain_allowed_false(self, cfg):
        assert cfg.is_domain_allowed("evil.com") is False


class TestPolicyBooleanFields:

    def test_review_shell_pipelines_false_is_honored(self):
        """When review_shell_pipelines is False, shell pipelines should not trigger review."""
        cfg = PolicyConfig.from_dict({"review_shell_pipelines": False, "allowed_commands": []})
        assert cfg.review_shell_pipelines is False

        from trpc_agent_sdk.tools.safety._bash_parser import BashParser
        parser = BashParser(cfg)
        findings = parser.parse("cat file | grep pattern")
        rule_ids = {f.rule_id for f in findings}
        # Should NOT have SHELL_PIPE_EXECUTION because review_shell_pipelines is False
        assert "R003_SHELL_PIPE_EXECUTION" not in rule_ids

    def test_review_package_install_false_is_honored(self):
        """When review_package_install is False, dependency installs should not be flagged."""
        cfg = PolicyConfig.from_dict({"review_package_install": False, "allowed_commands": []})
        assert cfg.review_package_install is False

        from trpc_agent_sdk.tools.safety._bash_parser import BashParser
        parser = BashParser(cfg)
        findings = parser.parse("pip install requests")
        rule_ids = {f.rule_id for f in findings}
        assert "R004_PIP_INSTALL" not in rule_ids

    def test_review_package_install_false_python(self):
        """When review_package_install is False, Python install patterns should not be flagged."""
        cfg = PolicyConfig.from_dict({"review_package_install": False})
        assert cfg.review_package_install is False

        from trpc_agent_sdk.tools.safety._python_parser import PythonParser
        parser = PythonParser(cfg)
        findings = parser.parse("# pip install requests")
        rule_ids = {f.rule_id for f in findings}
        assert "R004_PIP_INSTALL" not in rule_ids
