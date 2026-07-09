"""Unit tests for safety guard policy configuration loading and merging."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from trpc_agent_sdk.tools.safety.policy import (
    ENV_POLICY_PATH,
    FileOperationsPolicy,
    NetworkPolicy,
    PolicyConfig,
    ProcessPolicy,
    ResourcePolicy,
    _auto_discover_policy,
    _CONVENTION_FILENAMES,
    _CONVENTION_SUBDIRS,
    _default_policy,
    _merge_list,
    _merge_policies,
    load_policy,
)


class TestDefaultPolicy:
    """Test the built-in default policy completeness and correctness."""

    def test_default_policy_version(self):
        policy = _default_policy()
        assert policy.version == "1.0"

    def test_default_network_has_core_domains(self):
        policy = _default_policy()
        domains = policy.network.allowed_domains
        assert "api.openai.com" in domains
        assert "*.openai.com" in domains
        assert "*.googleapis.com" in domains
        assert "*.anthropic.com" in domains
        assert "*.githubusercontent.com" in domains
        assert "github.com" in domains
        assert "pypi.org" in domains
        assert "*.python.org" in domains
        assert "registry.npmjs.org" in domains
        assert "*.huggingface.co" in domains

    def test_default_process_has_safe_commands(self):
        policy = _default_policy()
        commands = policy.process.allowed_commands
        assert "python3" in commands
        assert "python" in commands
        assert "node" in commands
        assert "cat" in commands
        assert "ls" in commands
        assert "grep" in commands
        assert "echo" in commands

    def test_default_process_excludes_dangerous_commands(self):
        policy = _default_policy()
        commands = policy.process.allowed_commands
        assert "rm" not in commands
        assert "sudo" not in commands
        assert "curl" not in commands
        assert "wget" not in commands
        assert "chmod" not in commands
        assert "kill" not in commands

    def test_default_file_operations_has_sensitive_paths(self):
        policy = _default_policy()
        paths = policy.file_operations.forbidden_paths
        assert "/etc/" in paths
        assert "~/.ssh/" in paths
        assert "~/.aws/" in paths
        assert "~/.gnupg/" in paths
        assert "~/.config/" in paths
        assert "~/.env" in paths
        assert "/root/" in paths

    def test_default_resources_values(self):
        policy = _default_policy()
        assert policy.resources.max_timeout_seconds == 300
        assert policy.resources.max_output_size_mb == 100


class TestMergeList:
    """Test the _merge_list helper function."""

    def test_append_deduplicates(self):
        result = _merge_list(["a", "b", "c"], ["b", "c", "d"], override=False)
        assert result == ["a", "b", "c", "d"]

    def test_append_preserves_order(self):
        result = _merge_list(["x", "y"], ["z", "a"], override=False)
        assert result == ["x", "y", "z", "a"]

    def test_override_replaces_completely(self):
        result = _merge_list(["a", "b", "c"], ["x", "y"], override=True)
        assert result == ["x", "y"]

    def test_override_with_empty_user_list(self):
        result = _merge_list(["a", "b"], [], override=True)
        assert result == []

    def test_append_with_empty_user_list(self):
        result = _merge_list(["a", "b"], [], override=False)
        assert result == ["a", "b"]

    def test_append_with_empty_default_list(self):
        result = _merge_list([], ["x", "y"], override=False)
        assert result == ["x", "y"]


class TestMergePolicies:
    """Test the _merge_policies function."""

    def test_merge_appends_domains(self):
        default = _default_policy()
        user = PolicyConfig(network=NetworkPolicy(allowed_domains=["*.internal.corp", "api.myco.com"]))
        merged = _merge_policies(default, user)
        # Default domains preserved + user domains appended
        assert "api.openai.com" in merged.network.allowed_domains
        assert "*.internal.corp" in merged.network.allowed_domains
        assert "api.myco.com" in merged.network.allowed_domains

    def test_merge_appends_commands(self):
        default = _default_policy()
        user = PolicyConfig(process=ProcessPolicy(allowed_commands=["docker", "go"]))
        merged = _merge_policies(default, user)
        assert "python3" in merged.process.allowed_commands
        assert "docker" in merged.process.allowed_commands
        assert "go" in merged.process.allowed_commands

    def test_merge_appends_forbidden_paths(self):
        default = _default_policy()
        user = PolicyConfig(file_operations=FileOperationsPolicy(forbidden_paths=["~/secrets/"]))
        merged = _merge_policies(default, user)
        assert "/etc/" in merged.file_operations.forbidden_paths
        assert "~/secrets/" in merged.file_operations.forbidden_paths

    def test_merge_override_replaces_domains(self):
        default = _default_policy()
        user = PolicyConfig(network=NetworkPolicy(
            allowed_domains=["only-this.com"],
            override=True,
        ))
        merged = _merge_policies(default, user)
        assert merged.network.allowed_domains == ["only-this.com"]
        assert "api.openai.com" not in merged.network.allowed_domains

    def test_merge_override_replaces_commands(self):
        default = _default_policy()
        user = PolicyConfig(process=ProcessPolicy(
            allowed_commands=["custom-cmd"],
            override=True,
        ))
        merged = _merge_policies(default, user)
        assert merged.process.allowed_commands == ["custom-cmd"]
        assert "python3" not in merged.process.allowed_commands

    def test_merge_scalars_override(self):
        default = _default_policy()
        user = PolicyConfig(resources=ResourcePolicy(max_timeout_seconds=600, max_output_size_mb=200))
        merged = _merge_policies(default, user)
        assert merged.resources.max_timeout_seconds == 600
        assert merged.resources.max_output_size_mb == 200

    def test_merge_preserves_defaults_when_user_empty(self):
        default = _default_policy()
        user = PolicyConfig()  # All empty/default
        merged = _merge_policies(default, user)
        assert merged.network.allowed_domains == default.network.allowed_domains
        assert merged.process.allowed_commands == default.process.allowed_commands
        assert merged.file_operations.forbidden_paths == default.file_operations.forbidden_paths
        assert merged.resources.max_timeout_seconds == default.resources.max_timeout_seconds


class TestLoadPolicy:
    """Test load_policy function with various file scenarios."""

    def test_load_none_returns_default(self):
        policy = load_policy(None)
        default = _default_policy()
        assert policy.network.allowed_domains == default.network.allowed_domains
        assert policy.process.allowed_commands == default.process.allowed_commands

    def test_load_nonexistent_file_returns_default(self):
        policy = load_policy("/nonexistent/path/policy.yaml")
        default = _default_policy()
        assert policy.network.allowed_domains == default.network.allowed_domains

    def test_load_valid_policy_merges_with_default(self):
        data = {
            "network": {
                "allowed_domains": ["*.custom.io"]
            },
            "process": {
                "allowed_commands": ["docker"]
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            policy = load_policy(f.name)

        # User domains appended to defaults
        assert "*.custom.io" in policy.network.allowed_domains
        assert "api.openai.com" in policy.network.allowed_domains
        # User commands appended to defaults
        assert "docker" in policy.process.allowed_commands
        assert "python3" in policy.process.allowed_commands
        Path(f.name).unlink()

    def test_load_with_override_replaces_list(self):
        data = {
            "network": {
                "allowed_domains": ["only-mine.com"],
                "override": True,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            policy = load_policy(f.name)

        assert policy.network.allowed_domains == ["only-mine.com"]
        # Other sections still have defaults
        assert "python3" in policy.process.allowed_commands
        Path(f.name).unlink()

    def test_load_with_scalar_override(self):
        data = {
            "resources": {
                "max_timeout_seconds": 600,
                "max_output_size_mb": 50,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            policy = load_policy(f.name)

        assert policy.resources.max_timeout_seconds == 600
        assert policy.resources.max_output_size_mb == 50
        Path(f.name).unlink()

    def test_load_empty_yaml_returns_default(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            policy = load_policy(f.name)

        default = _default_policy()
        assert policy.network.allowed_domains == default.network.allowed_domains
        Path(f.name).unlink()

    def test_load_invalid_yaml_returns_default(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("{{{{invalid yaml:::::")
            f.flush()
            policy = load_policy(f.name)

        default = _default_policy()
        assert policy.network.allowed_domains == default.network.allowed_domains
        Path(f.name).unlink()

    def test_load_yaml_with_list_instead_of_dict_returns_default(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(["item1", "item2"], f)
            f.flush()
            policy = load_policy(f.name)

        default = _default_policy()
        assert policy.network.allowed_domains == default.network.allowed_domains
        Path(f.name).unlink()

    def test_load_with_path_object(self):
        data = {"network": {"allowed_domains": ["path-test.com"]}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            policy = load_policy(Path(f.name))

        assert "path-test.com" in policy.network.allowed_domains
        Path(f.name).unlink()

    def test_load_ignores_unknown_fields(self):
        """Unknown top-level fields are ignored (forward compatibility)."""
        data = {
            "version": "1.0",
            "network": {
                "allowed_domains": ["ok.com"]
            },
            "future_field": {
                "some_key": "some_value"
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            policy = load_policy(f.name)

        assert "ok.com" in policy.network.allowed_domains
        Path(f.name).unlink()

    def test_load_partial_policy_preserves_defaults(self):
        """A file with only one section still gets full defaults for other sections."""
        data = {"file_operations": {"forbidden_paths": ["~/my_secret/"]}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            f.flush()
            policy = load_policy(f.name)

        # User path appended
        assert "~/my_secret/" in policy.file_operations.forbidden_paths
        # Default paths preserved
        assert "/etc/" in policy.file_operations.forbidden_paths
        # Other sections fully defaulted
        assert "api.openai.com" in policy.network.allowed_domains
        assert "python3" in policy.process.allowed_commands
        Path(f.name).unlink()


class TestPolicyConfig:
    """Test PolicyConfig model construction and validation."""

    def test_default_construction(self):
        policy = PolicyConfig()
        assert policy.version == "1.0"
        assert policy.network.allowed_domains == []
        assert policy.process.allowed_commands == []
        assert policy.file_operations.forbidden_paths == []
        assert policy.resources.max_timeout_seconds == 300
        assert policy.resources.max_output_size_mb == 100

    def test_serialization_roundtrip(self):
        policy = _default_policy()
        data = policy.model_dump()
        restored = PolicyConfig(**data)
        assert restored.network.allowed_domains == policy.network.allowed_domains
        assert restored.process.allowed_commands == policy.process.allowed_commands
        assert restored.file_operations.forbidden_paths == policy.file_operations.forbidden_paths
        assert restored.resources.max_timeout_seconds == policy.resources.max_timeout_seconds

    def test_glob_patterns_stored_as_is(self):
        """Glob patterns are stored verbatim; matching is done by rules."""
        policy = PolicyConfig(network=NetworkPolicy(allowed_domains=["*.example.com", "api.*.internal"]))
        assert "*.example.com" in policy.network.allowed_domains
        assert "api.*.internal" in policy.network.allowed_domains


class TestAutoDiscoverPolicy:
    """Test the _auto_discover_policy function and load_policy auto-discovery."""

    def test_discover_via_env_var(self, tmp_path):
        """ENV_POLICY_PATH takes highest priority."""
        policy_file = tmp_path / "custom_policy.yaml"
        policy_file.write_text(yaml.dump({"network": {"allowed_domains": ["env-test.com"]}}))
        with patch.dict(os.environ, {ENV_POLICY_PATH: str(policy_file)}):
            result = _auto_discover_policy()
            assert result == policy_file

    def test_discover_env_var_nonexistent_file(self, tmp_path):
        """If ENV points to nonexistent file, continues discovery."""
        with patch.dict(os.environ, {ENV_POLICY_PATH: "/nonexistent/policy.yaml"}):
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                result = _auto_discover_policy()
                assert result is None

    def test_discover_in_cwd(self, tmp_path):
        """Convention file in CWD is found."""
        policy_file = tmp_path / "tool_safety_policy.yaml"
        policy_file.write_text(yaml.dump({"network": {"allowed_domains": ["cwd-test.com"]}}))
        with patch.dict(os.environ, {}, clear=False):
            # Remove ENV_POLICY_PATH if set
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                result = _auto_discover_policy()
                assert result == policy_file

    def test_discover_yml_extension(self, tmp_path):
        """Convention file with .yml extension is also found."""
        policy_file = tmp_path / "tool_safety_policy.yml"
        policy_file.write_text(yaml.dump({"network": {"allowed_domains": ["yml-test.com"]}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                result = _auto_discover_policy()
                assert result == policy_file

    def test_discover_in_safety_subdir(self, tmp_path):
        """Convention file in .safety/ subdir is found."""
        safety_dir = tmp_path / ".safety"
        safety_dir.mkdir()
        policy_file = safety_dir / "tool_safety_policy.yaml"
        policy_file.write_text(yaml.dump({"network": {"allowed_domains": ["subdir-test.com"]}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                result = _auto_discover_policy()
                assert result == policy_file

    def test_discover_in_config_subdir(self, tmp_path):
        """Convention file in config/ subdir is found."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        policy_file = config_dir / "tool_safety_policy.yaml"
        policy_file.write_text(yaml.dump({"network": {"allowed_domains": ["config-test.com"]}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                result = _auto_discover_policy()
                assert result == policy_file

    def test_discover_cwd_takes_priority_over_subdir(self, tmp_path):
        """CWD root file has higher priority than subdir."""
        # Create both CWD and .safety versions
        cwd_file = tmp_path / "tool_safety_policy.yaml"
        cwd_file.write_text(yaml.dump({"network": {"allowed_domains": ["cwd-priority.com"]}}))
        safety_dir = tmp_path / ".safety"
        safety_dir.mkdir()
        subdir_file = safety_dir / "tool_safety_policy.yaml"
        subdir_file.write_text(yaml.dump({"network": {"allowed_domains": ["subdir.com"]}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                result = _auto_discover_policy()
                assert result == cwd_file

    def test_discover_returns_none_when_no_file(self, tmp_path):
        """Returns None when no convention file exists anywhere."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                result = _auto_discover_policy()
                assert result is None

    def test_load_policy_none_triggers_auto_discovery(self, tmp_path):
        """load_policy(None) uses auto-discovery when file is present."""
        policy_file = tmp_path / "tool_safety_policy.yaml"
        policy_file.write_text(yaml.dump({"network": {"allowed_domains": ["auto-discover.io"]}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                policy = load_policy(None)
                # User domain appended to defaults
                assert "auto-discover.io" in policy.network.allowed_domains
                # Default domains also present
                assert "api.openai.com" in policy.network.allowed_domains

    def test_load_policy_none_returns_default_when_no_file(self, tmp_path):
        """load_policy(None) returns default when no convention file found."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_POLICY_PATH, None)
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                policy = load_policy(None)
                default = _default_policy()
                assert policy.network.allowed_domains == default.network.allowed_domains

    def test_load_policy_explicit_path_still_works(self, tmp_path):
        """Explicit path argument bypasses auto-discovery."""
        policy_file = tmp_path / "my_custom.yaml"
        policy_file.write_text(yaml.dump({"network": {"allowed_domains": ["explicit.com"]}}))
        policy = load_policy(str(policy_file))
        assert "explicit.com" in policy.network.allowed_domains

    def test_env_var_takes_priority_over_cwd(self, tmp_path):
        """ENV_POLICY_PATH has higher priority than CWD file."""
        # Create file via env var
        env_file = tmp_path / "env_policy.yaml"
        env_file.write_text(yaml.dump({"network": {"allowed_domains": ["env-priority.com"]}}))
        # Also create file in CWD
        cwd_file = tmp_path / "tool_safety_policy.yaml"
        cwd_file.write_text(yaml.dump({"network": {"allowed_domains": ["cwd.com"]}}))
        with patch.dict(os.environ, {ENV_POLICY_PATH: str(env_file)}):
            with patch("trpc_agent_sdk.tools.safety.policy.Path.cwd", return_value=tmp_path):
                policy = load_policy(None)
                assert "env-priority.com" in policy.network.allowed_domains
