"""Tests for trpc_agent_sdk.server.openclaw.config._config."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from trpc_agent_sdk.server.openclaw.config._config import (
    ClawConfig,
    AgentConfig,
    _expand_env_vars,
    _read_config_file,
    create_inner_dirs_and_files,
    load_config,
)
from trpc_agent_sdk.server.openclaw.config._constants import (
    AGENT_FILE_NAME,
    DEFAULT_WORKSPACE_PATH,
    HISTORY_FILE_NAME,
    MEMORY_FILE_NAME,
    SOUL_FILE_NAME,
    TOOL_FILE_NAME,
    TRPC_CLAW_CONFIG,
    USER_FILE_NAME,
)


class TestClawConfig:
    """Tests for ClawConfig model and its properties."""

    def test_default_config(self):
        cfg = ClawConfig()
        assert cfg.agent is not None
        assert cfg.skills is not None
        assert cfg.storage is not None

    def test_workspace_property(self, tmp_path):
        cfg = ClawConfig()
        cfg.agent.workspace = str(tmp_path / "ws")
        result = cfg.workspace
        assert result == (tmp_path / "ws").resolve()
        assert isinstance(result, Path)

    def test_workspace_expands_user(self):
        cfg = ClawConfig()
        cfg.agent.workspace = "~/some_workspace"
        result = cfg.workspace
        assert "~" not in str(result)
        assert result.is_absolute()

    def test_model_name_property(self):
        cfg = ClawConfig()
        cfg.agent.model = "gpt-4"
        assert cfg.model_name == "gpt-4"

    def test_model_api_key_property(self):
        cfg = ClawConfig()
        cfg.agent.api_key = "sk-test-key"
        assert cfg.model_api_key == "sk-test-key"

    def test_model_base_url_property(self):
        cfg = ClawConfig()
        cfg.agent.api_base = "https://api.example.com"
        assert cfg.model_base_url == "https://api.example.com"

    def test_model_extra_headers_property(self):
        cfg = ClawConfig()
        cfg.agent.extra_headers = {"X-Custom": "value"}
        assert cfg.model_extra_headers == {"X-Custom": "value"}

    def test_skill_roots_property(self):
        cfg = ClawConfig()
        cfg.skills.skill_roots = ["/path/a", "/path/b"]
        assert cfg.skill_roots == ["/path/a", "/path/b"]


class TestReadConfigFile:
    """Tests for _read_config_file."""

    def test_nonexistent_file(self, tmp_path):
        result = _read_config_file(tmp_path / "missing.yaml")
        assert result == {}

    def test_yaml_file(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump({"agent": {"model": "gpt-4"}}), encoding="utf-8")
        result = _read_config_file(cfg_path)
        assert result == {"agent": {"model": "gpt-4"}}

    def test_yml_extension(self, tmp_path):
        cfg_path = tmp_path / "config.yml"
        cfg_path.write_text(yaml.dump({"key": "val"}), encoding="utf-8")
        result = _read_config_file(cfg_path)
        assert result == {"key": "val"}

    def test_json_file(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"agent": {"api_key": "abc"}}), encoding="utf-8")
        result = _read_config_file(cfg_path)
        assert result == {"agent": {"api_key": "abc"}}

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("", encoding="utf-8")
        result = _read_config_file(cfg_path)
        assert result == {}

    def test_empty_json_raises(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("", encoding="utf-8")
        with pytest.raises(Exception):
            _read_config_file(cfg_path)

    def test_invalid_yaml_format_raises(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("- list\n- items\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML config format"):
            _read_config_file(cfg_path)

    def test_invalid_json_format_raises(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON config format"):
            _read_config_file(cfg_path)

    def test_yaml_case_insensitive_extension(self, tmp_path):
        cfg_path = tmp_path / "config.YAML"
        cfg_path.write_text(yaml.dump({"x": 1}), encoding="utf-8")
        result = _read_config_file(cfg_path)
        assert result == {"x": 1}


class TestExpandEnvVars:
    """Tests for _expand_env_vars."""

    def test_string_expansion(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert _expand_env_vars("$MY_VAR") == "hello"

    def test_string_no_var(self):
        assert _expand_env_vars("plain") == "plain"

    def test_list_expansion(self, monkeypatch):
        monkeypatch.setenv("V1", "a")
        monkeypatch.setenv("V2", "b")
        result = _expand_env_vars(["$V1", "$V2"])
        assert result == ["a", "b"]

    def test_dict_expansion(self, monkeypatch):
        monkeypatch.setenv("DV", "expanded")
        result = _expand_env_vars({"key": "$DV"})
        assert result == {"key": "expanded"}

    def test_nested_dict_in_list(self, monkeypatch):
        monkeypatch.setenv("NV", "deep")
        result = _expand_env_vars([{"inner": "$NV"}])
        assert result == [{"inner": "deep"}]

    def test_non_string_passthrough_int(self):
        assert _expand_env_vars(42) == 42

    def test_non_string_passthrough_none(self):
        assert _expand_env_vars(None) is None

    def test_non_string_passthrough_bool(self):
        assert _expand_env_vars(True) is True


class TestCreateInnerDirsAndFiles:
    """Tests for create_inner_dirs_and_files."""

    def test_creates_expected_structure(self, tmp_path):
        cfg = ClawConfig()
        cfg.agent.workspace = str(tmp_path / "ws")
        cfg.skills.local_config.workspace = str(tmp_path / "skills_ws")
        create_inner_dirs_and_files(cfg)

        ws = tmp_path / "ws"
        assert (ws / "sessions").is_dir()
        assert (ws / SOUL_FILE_NAME).is_file()
        assert (ws / USER_FILE_NAME).is_file()
        assert (ws / TOOL_FILE_NAME).is_file()
        assert (ws / AGENT_FILE_NAME).is_file()
        assert (ws / "memory").is_dir()
        assert (ws / "memory" / HISTORY_FILE_NAME).is_file()
        assert (ws / "memory" / MEMORY_FILE_NAME).is_file()
        assert (ws / "skills").is_dir()
        assert (tmp_path / "skills_ws").is_dir()

    def test_idempotent(self, tmp_path):
        cfg = ClawConfig()
        cfg.agent.workspace = str(tmp_path / "ws")
        cfg.skills.local_config.workspace = str(tmp_path / "skills_ws")
        create_inner_dirs_and_files(cfg)
        create_inner_dirs_and_files(cfg)
        assert (tmp_path / "ws" / "sessions").is_dir()


class TestLoadConfig:
    """Tests for load_config."""

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_explicit_path_yaml(self, mock_set_config, tmp_path):
        cfg_path = tmp_path / "test.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(
            yaml.dump({"agent": {"workspace": str(ws), "model": "gpt-4", "api_key": "key123"}}),
            encoding="utf-8",
        )
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_name == "gpt-4"
        assert cfg.model_api_key == "key123"
        mock_set_config.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_explicit_path_json(self, mock_set_config, tmp_path):
        cfg_path = tmp_path / "test.json"
        ws = tmp_path / "workspace"
        cfg_path.write_text(
            json.dumps({"agent": {"workspace": str(ws), "model": "test-model"}}),
            encoding="utf-8",
        )
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_name == "test-model"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_env_var_path(self, mock_set_config, tmp_path, monkeypatch):
        cfg_path = tmp_path / "env_cfg.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(
            yaml.dump({"agent": {"workspace": str(ws), "api_key": "envkey"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv(TRPC_CLAW_CONFIG, str(cfg_path))
        cfg = load_config()
        assert cfg.model_api_key == "envkey"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    @patch("trpc_agent_sdk.server.openclaw.config._config.DEFAULT_TRPC_CLAW_DIR")
    @patch("trpc_agent_sdk.server.openclaw.config._config.DEFAULT_CONFIG_PATH")
    def test_default_path_fallback(self, mock_default_path, mock_default_dir, mock_set_config, tmp_path, monkeypatch):
        monkeypatch.delenv(TRPC_CLAW_CONFIG, raising=False)
        default_dir = tmp_path / ".trpc_agent_claw"
        default_dir.mkdir()
        mock_default_dir.exists.return_value = True
        cfg_file = default_dir / "config.yaml"
        cfg_file.touch()
        mock_default_path.__str__ = lambda s: str(cfg_file)
        mock_default_path.exists.return_value = True
        mock_default_path.suffix = ".yaml"
        # Actually write config to the file
        ws = tmp_path / "workspace"
        cfg_file.write_text(yaml.dump({"agent": {"workspace": str(ws)}}), encoding="utf-8")
        # Patch the Path() constructor to use our mock path properly
        cfg = load_config()
        assert isinstance(cfg, ClawConfig)

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_legacy_agents_key(self, mock_set_config, tmp_path):
        cfg_path = tmp_path / "legacy.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(
            yaml.dump({"agents": {"workspace": str(ws), "model": "legacy-model"}}),
            encoding="utf-8",
        )
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_name == "legacy-model"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_env_override_api_key(self, mock_set_config, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(yaml.dump({"agent": {"workspace": str(ws)}}), encoding="utf-8")
        monkeypatch.setenv("TRPC_AGENT_API_KEY", "env-api-key")
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_api_key == "env-api-key"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_env_override_base_url(self, mock_set_config, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(yaml.dump({"agent": {"workspace": str(ws)}}), encoding="utf-8")
        monkeypatch.setenv("TRPC_AGENT_BASE_URL", "https://env.example.com")
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_base_url == "https://env.example.com"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_env_override_model_when_no_agent_in_raw(self, mock_set_config, tmp_path, monkeypatch):
        """Model env override applies when 'agent' key is absent from raw config."""
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text("", encoding="utf-8")
        monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "env-model")
        monkeypatch.setattr(
            "trpc_agent_sdk.server.openclaw.config._config.DEFAULT_WORKSPACE_PATH",
            tmp_path / "workspace",
        )
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_name == "env-model"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_env_override_model_when_model_empty(self, mock_set_config, tmp_path, monkeypatch):
        """Model env override applies when agent.model is empty."""
        cfg_path = tmp_path / "cfg.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(
            yaml.dump({"agent": {"workspace": str(ws), "model": ""}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "env-model")
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_name == "env-model"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_skills_workspace_default(self, mock_set_config, tmp_path):
        cfg_path = tmp_path / "cfg.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(yaml.dump({"agent": {"workspace": str(ws)}}), encoding="utf-8")
        cfg = load_config(config_path=cfg_path)
        assert cfg.skills.local_config.workspace == f"{ws}/skills_ws"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_need_default_workspace_when_empty_raw(self, mock_set_config, tmp_path, monkeypatch):
        default_ws = tmp_path / "default_workspace"
        monkeypatch.setattr(
            "trpc_agent_sdk.server.openclaw.config._config.DEFAULT_WORKSPACE_PATH",
            default_ws,
        )
        cfg_path = tmp_path / "empty.yaml"
        cfg_path.write_text("", encoding="utf-8")
        cfg = load_config(config_path=cfg_path)
        assert cfg.agent.workspace == str(default_ws)

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_need_default_workspace_when_no_workspace_key(self, mock_set_config, tmp_path, monkeypatch):
        default_ws = tmp_path / "default_workspace"
        monkeypatch.setattr(
            "trpc_agent_sdk.server.openclaw.config._config.DEFAULT_WORKSPACE_PATH",
            default_ws,
        )
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({"agent": {"model": "gpt-4"}}), encoding="utf-8")
        cfg = load_config(config_path=cfg_path)
        assert cfg.agent.workspace == str(default_ws)

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_api_key_in_config_not_overridden(self, mock_set_config, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(
            yaml.dump({"agent": {"workspace": str(ws), "api_key": "file-key"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("TRPC_AGENT_API_KEY", "env-key")
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_api_key == "file-key"

    @patch("trpc_agent_sdk.server.openclaw.config._config.set_config_path")
    def test_expand_env_vars_in_config(self, mock_set_config, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_MODEL", "expanded-model")
        cfg_path = tmp_path / "cfg.yaml"
        ws = tmp_path / "workspace"
        cfg_path.write_text(
            yaml.dump({"agent": {"workspace": str(ws), "model": "$MY_MODEL"}}),
            encoding="utf-8",
        )
        cfg = load_config(config_path=cfg_path)
        assert cfg.model_name == "expanded-model"
