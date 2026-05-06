# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.code_executors.cube._types."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.code_executors.cube._types import (
    DEFAULT_EXECUTE_TIMEOUT,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_REMOTE_WORKSPACE,
    ENV_API_KEY,
    ENV_API_URL,
    ENV_TEMPLATE,
    CubeCodeExecutorConfig,
    CubeWorkspaceRuntimeConfig,
)


class TestCubeCodeExecutorConfigDefaults:

    def test_defaults(self):
        cfg = CubeCodeExecutorConfig()
        assert cfg.template is None
        assert cfg.api_url is None
        assert cfg.api_key is None
        assert cfg.sandbox_id is None
        assert cfg.execute_timeout == DEFAULT_EXECUTE_TIMEOUT == 60.0
        assert cfg.idle_timeout == DEFAULT_IDLE_TIMEOUT == 3600
        assert isinstance(cfg.idle_timeout, int)

    def test_env_var_names(self):
        # The implementation uses these names; if they change, hermes and
        # every downstream deployment doc changes with them. Pin them.
        assert ENV_API_URL == "E2B_API_URL"
        assert ENV_API_KEY == "E2B_API_KEY"
        assert ENV_TEMPLATE == "CUBE_TEMPLATE_ID"


class TestCubeCodeExecutorConfigValidation:

    def test_rejects_float_idle_timeout(self):
        with pytest.raises(TypeError, match="idle_timeout must be an int"):
            CubeCodeExecutorConfig(idle_timeout=0.9)  # type: ignore[arg-type]

    def test_rejects_bool_idle_timeout(self):
        # bool is a subclass of int in Python; explicitly reject it so
        # ``idle_timeout=True`` doesn't silently become ``1`` second.
        with pytest.raises(TypeError, match="idle_timeout must be an int"):
            CubeCodeExecutorConfig(idle_timeout=True)  # type: ignore[arg-type]

    def test_rejects_zero_idle_timeout(self):
        with pytest.raises(ValueError, match="idle_timeout must be >= 1"):
            CubeCodeExecutorConfig(idle_timeout=0)

    def test_rejects_negative_idle_timeout(self):
        with pytest.raises(ValueError, match="idle_timeout must be >= 1"):
            CubeCodeExecutorConfig(idle_timeout=-5)

    def test_rejects_non_positive_execute_timeout(self):
        with pytest.raises(ValueError, match="execute_timeout must be > 0"):
            CubeCodeExecutorConfig(execute_timeout=0)
        with pytest.raises(ValueError, match="execute_timeout must be > 0"):
            CubeCodeExecutorConfig(execute_timeout=-1.0)

    def test_accepts_minimum_idle_timeout(self):
        cfg = CubeCodeExecutorConfig(idle_timeout=1)
        assert cfg.idle_timeout == 1

    def test_accepts_subsecond_execute_timeout(self):
        cfg = CubeCodeExecutorConfig(execute_timeout=0.25)
        assert cfg.execute_timeout == 0.25


class TestResolveTemplate:

    def test_uses_field_when_set(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPLATE, "env-template")
        cfg = CubeCodeExecutorConfig(template="explicit")
        assert cfg.resolve_template() == "explicit"

    def test_falls_back_to_env(self, monkeypatch):
        monkeypatch.delenv(ENV_TEMPLATE, raising=False)
        monkeypatch.setenv(ENV_TEMPLATE, "env-template")
        cfg = CubeCodeExecutorConfig()
        assert cfg.resolve_template() == "env-template"

    def test_missing_both_raises(self, monkeypatch):
        monkeypatch.delenv(ENV_TEMPLATE, raising=False)
        cfg = CubeCodeExecutorConfig()
        with pytest.raises(ValueError, match=ENV_TEMPLATE):
            cfg.resolve_template()


class TestResolveApiUrl:

    def test_uses_field_when_set(self, monkeypatch):
        monkeypatch.setenv(ENV_API_URL, "https://env")
        cfg = CubeCodeExecutorConfig(api_url="https://explicit")
        assert cfg.resolve_api_url() == "https://explicit"

    def test_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv(ENV_API_URL, "https://env")
        cfg = CubeCodeExecutorConfig()
        assert cfg.resolve_api_url() == "https://env"

    def test_missing_both_raises(self, monkeypatch):
        monkeypatch.delenv(ENV_API_URL, raising=False)
        cfg = CubeCodeExecutorConfig()
        with pytest.raises(ValueError, match=ENV_API_URL):
            cfg.resolve_api_url()


class TestResolveApiKey:

    def test_uses_field_when_set(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, "env-key")
        cfg = CubeCodeExecutorConfig(api_key="explicit-key")
        assert cfg.resolve_api_key() == "explicit-key"

    def test_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, "env-key")
        cfg = CubeCodeExecutorConfig()
        assert cfg.resolve_api_key() == "env-key"

    def test_missing_both_raises(self, monkeypatch):
        monkeypatch.delenv(ENV_API_KEY, raising=False)
        cfg = CubeCodeExecutorConfig()
        with pytest.raises(ValueError, match=ENV_API_KEY):
            cfg.resolve_api_key()


class TestCubeWorkspaceRuntimeConfig:

    def test_default_remote_workspace(self):
        cfg = CubeWorkspaceRuntimeConfig()
        assert cfg.remote_workspace == DEFAULT_REMOTE_WORKSPACE == "/workspace/cube_agent"

    def test_custom_remote_workspace_preserved(self):
        cfg = CubeWorkspaceRuntimeConfig(remote_workspace="/ws/custom")
        assert cfg.remote_workspace == "/ws/custom"
