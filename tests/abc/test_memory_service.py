# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.abc._memory_service.

Covers:
- MemoryServiceConfig: defaults, create_ttl_config, clean_ttl_config
- MemoryServiceABC: __init__ logic (auto-create config), enabled property
"""

from __future__ import annotations

import time
from typing import Optional

import pytest

from trpc_agent_sdk.abc._memory_service import MemoryServiceABC, MemoryServiceConfig
from trpc_agent_sdk.abc._session import SessionABC
from trpc_agent_sdk.types import DEFAULT_CLEANUP_INTERVAL_SECONDS, DEFAULT_TTL_SECONDS, SearchMemoryResponse


class StubMemoryService(MemoryServiceABC):
    """Minimal concrete memory service for testing base class logic."""

    async def store_session(self, session, agent_context=None):
        pass  # pragma: no cover

    async def search_memory(self, key, query, limit=10, agent_context=None):
        return SearchMemoryResponse(memories=[])  # pragma: no cover

    async def close(self):
        pass  # pragma: no cover


class TestMemoryServiceConfig:
    """Tests for MemoryServiceConfig."""

    def test_defaults(self):
        cfg = MemoryServiceConfig()
        assert cfg.enabled is False
        assert cfg.ttl is not None

    def test_enabled_true(self):
        cfg = MemoryServiceConfig(enabled=True)
        assert cfg.enabled is True

    def test_create_ttl_config_defaults(self):
        ttl = MemoryServiceConfig.create_ttl_config()
        assert ttl.enable is True
        assert ttl.ttl_seconds == DEFAULT_TTL_SECONDS
        assert ttl.cleanup_interval_seconds == DEFAULT_CLEANUP_INTERVAL_SECONDS
        assert ttl.update_time > 0

    def test_create_ttl_config_custom(self):
        ttl = MemoryServiceConfig.create_ttl_config(
            enable=False,
            ttl_seconds=3600,
            cleanup_interval_seconds=120.0,
        )
        assert ttl.enable is False
        assert ttl.ttl_seconds == 3600
        assert ttl.cleanup_interval_seconds == 120.0

    def test_clean_ttl_config(self):
        cfg = MemoryServiceConfig(enabled=True)
        cfg.ttl = MemoryServiceConfig.create_ttl_config()
        cfg.clean_ttl_config()
        assert cfg.ttl.enable is False
        assert cfg.ttl.ttl_seconds == 0
        assert cfg.ttl.cleanup_interval_seconds == 0.0
        assert cfg.ttl.update_time == 0.0


class TestMemoryServiceABCInit:
    """Tests for MemoryServiceABC.__init__ logic."""

    def test_init_with_default_config(self):
        svc = StubMemoryService()
        assert svc.enabled is False
        assert svc._memory_service_config is not None
        assert svc._memory_service_config.ttl.enable is False

    def test_init_with_enabled_true(self):
        svc = StubMemoryService(enabled=True)
        assert svc.enabled is True

    def test_init_with_custom_config(self):
        cfg = MemoryServiceConfig(enabled=True)
        svc = StubMemoryService(memory_service_config=cfg)
        assert svc.enabled is True
        assert svc._memory_service_config is cfg

    def test_init_none_config_creates_default_and_cleans(self):
        """When memory_service_config is None, a default config is created and TTL is cleaned."""
        svc = StubMemoryService(memory_service_config=None, enabled=False)
        cfg = svc._memory_service_config
        assert cfg.enabled is False
        assert cfg.ttl.enable is False
        assert cfg.ttl.ttl_seconds == 0

    def test_init_custom_config_not_cleaned(self):
        """When a custom config is provided, clean_ttl_config is NOT called."""
        ttl = MemoryServiceConfig.create_ttl_config(enable=True, ttl_seconds=7200)
        cfg = MemoryServiceConfig(enabled=True, ttl=ttl)
        svc = StubMemoryService(memory_service_config=cfg)
        assert svc._memory_service_config.ttl.enable is True
        assert svc._memory_service_config.ttl.ttl_seconds == 7200

    def test_enabled_property_reflects_config(self):
        cfg = MemoryServiceConfig(enabled=False)
        svc = StubMemoryService(memory_service_config=cfg)
        assert svc.enabled is False
        cfg.enabled = True
        assert svc.enabled is True
