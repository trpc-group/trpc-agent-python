# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for PromptCacheConfig and LLMModel._resolve_prompt_cache_config.

These tests verify:
- PromptCacheConfig default field values
- Resolution priority: model-level vs run-level config
- Merge semantics: run config overrides only explicitly set fields
- Disabled configs are suppressed
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.configs import PromptCacheConfig, RunConfig
from trpc_agent_sdk.models import AnthropicModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model(**kwargs) -> AnthropicModel:
    """Return a minimal AnthropicModel (concrete subclass of LLMModel for resolution tests)."""
    kwargs.setdefault("model_name", "claude-3-5-sonnet-20241022")
    kwargs.setdefault("api_key", "test-key")
    return AnthropicModel(**kwargs)


def _ctx(prompt_cache: PromptCacheConfig | None = None) -> MagicMock:
    """Return a lightweight mock InvocationContext with a RunConfig."""
    ctx = MagicMock()
    ctx.run_config = RunConfig(prompt_cache=prompt_cache)
    return ctx


# ---------------------------------------------------------------------------
# PromptCacheConfig defaults
# ---------------------------------------------------------------------------


class TestPromptCacheConfigDefaults:
    """PromptCacheConfig should have sensible defaults out of the box."""

    def test_disabled_by_default(self):
        cfg = PromptCacheConfig()
        assert cfg.enabled is False

    def test_breakpoints_default_to_system(self):
        cfg = PromptCacheConfig()
        assert cfg.breakpoints == ["system"]

    def test_ttl_default_is_none(self):
        cfg = PromptCacheConfig()
        assert cfg.ttl is None

    def test_prompt_cache_key_default_is_none(self):
        cfg = PromptCacheConfig()
        assert cfg.prompt_cache_key is None


# ---------------------------------------------------------------------------
# _resolve_prompt_cache_config: no config present
# ---------------------------------------------------------------------------


class TestResolvePromptCacheConfigNoConfig:
    """When neither model-level nor run-level config is set, resolver returns None."""

    def test_no_model_config_no_ctx_returns_none(self):
        model = _model()
        assert model._resolve_prompt_cache_config(None) is None

    def test_no_model_config_ctx_without_run_cache_returns_none(self):
        model = _model()
        ctx = _ctx(prompt_cache=None)
        assert model._resolve_prompt_cache_config(ctx) is None


# ---------------------------------------------------------------------------
# _resolve_prompt_cache_config: disabled configs
# ---------------------------------------------------------------------------


class TestResolvePromptCacheConfigDisabled:
    """Disabled configs (enabled=False) must always return None."""

    def test_model_config_disabled_returns_none(self):
        cache_cfg = PromptCacheConfig(enabled=False)
        model = _model(prompt_cache_config=cache_cfg)
        assert model._resolve_prompt_cache_config(None) is None

    def test_run_config_disabled_returns_none(self):
        run_cache = PromptCacheConfig(enabled=False)
        model = _model()
        ctx = _ctx(prompt_cache=run_cache)
        assert model._resolve_prompt_cache_config(ctx) is None

    def test_model_config_enabled_run_config_disabled_returns_none(self):
        """Per-run enabled=False must suppress a model-level enabled config."""
        model_cache = PromptCacheConfig(enabled=True, ttl="1h", breakpoints=["system"])
        run_cache = PromptCacheConfig(enabled=False)
        model = _model(prompt_cache_config=model_cache)
        ctx = _ctx(prompt_cache=run_cache)
        assert model._resolve_prompt_cache_config(ctx) is None


# ---------------------------------------------------------------------------
# _resolve_prompt_cache_config: enabled configs
# ---------------------------------------------------------------------------


class TestResolvePromptCacheConfigEnabled:
    """Enabled configs are returned and merged correctly."""

    def test_model_level_enabled_no_ctx(self):
        """Model-level enabled config is returned when there is no run context."""
        cache_cfg = PromptCacheConfig(enabled=True, ttl="1h", breakpoints=["system", "tools"])
        model = _model(prompt_cache_config=cache_cfg)
        resolved = model._resolve_prompt_cache_config(None)
        assert resolved is not None
        assert resolved.enabled is True
        assert resolved.ttl == "1h"
        assert resolved.breakpoints == ["system", "tools"]

    def test_run_level_enabled_no_model_config(self):
        """Run-level config is used when no model-level config exists."""
        run_cache = PromptCacheConfig(enabled=True, ttl="5m")
        model = _model()
        ctx = _ctx(prompt_cache=run_cache)
        resolved = model._resolve_prompt_cache_config(ctx)
        assert resolved is not None
        assert resolved.enabled is True
        assert resolved.ttl == "5m"

    def test_model_level_enabled_ctx_with_no_run_cache(self):
        """Model-level config is used when ctx has no run-level cache config."""
        cache_cfg = PromptCacheConfig(enabled=True, prompt_cache_key="my-key")
        model = _model(prompt_cache_config=cache_cfg)
        ctx = _ctx(prompt_cache=None)
        resolved = model._resolve_prompt_cache_config(ctx)
        assert resolved is not None
        assert resolved.prompt_cache_key == "my-key"


# ---------------------------------------------------------------------------
# _resolve_prompt_cache_config: merge semantics
# ---------------------------------------------------------------------------


class TestResolvePromptCacheConfigMerge:
    """Run config overrides only explicitly-set fields; model baseline is preserved."""

    def test_run_config_overrides_ttl_only(self):
        """Run config sets ttl; model's breakpoints and prompt_cache_key are preserved."""
        model_cache = PromptCacheConfig(
            enabled=True,
            ttl="1h",
            breakpoints=["system", "tools"],
            prompt_cache_key="base-key",
        )
        # Run config only explicitly sets ttl (enabled must be True to pass resolver)
        run_cache = PromptCacheConfig(enabled=True, ttl="5m")
        model = _model(prompt_cache_config=model_cache)
        ctx = _ctx(prompt_cache=run_cache)
        resolved = model._resolve_prompt_cache_config(ctx)
        assert resolved is not None
        # run overrides ttl
        assert resolved.ttl == "5m"
        # model baseline fields not set in run config are preserved
        assert resolved.breakpoints == ["system", "tools"]
        assert resolved.prompt_cache_key == "base-key"

    def test_run_config_overrides_prompt_cache_key_only(self):
        """Run config sets prompt_cache_key; model's ttl and breakpoints are preserved."""
        model_cache = PromptCacheConfig(
            enabled=True,
            ttl="1h",
            breakpoints=["system"],
        )
        run_cache = PromptCacheConfig(enabled=True, prompt_cache_key="override-key")
        model = _model(prompt_cache_config=model_cache)
        ctx = _ctx(prompt_cache=run_cache)
        resolved = model._resolve_prompt_cache_config(ctx)
        assert resolved is not None
        assert resolved.prompt_cache_key == "override-key"
        assert resolved.ttl == "1h"
        assert resolved.breakpoints == ["system"]

    def test_run_config_overrides_breakpoints(self):
        """Run config breakpoints field overrides the model-level list."""
        model_cache = PromptCacheConfig(enabled=True, breakpoints=["system"])
        run_cache = PromptCacheConfig(enabled=True, breakpoints=["tools", "messages"])
        model = _model(prompt_cache_config=model_cache)
        ctx = _ctx(prompt_cache=run_cache)
        resolved = model._resolve_prompt_cache_config(ctx)
        assert resolved is not None
        assert resolved.breakpoints == ["tools", "messages"]
