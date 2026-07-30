# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.sessions._types.

Covers:
- SessionServiceConfig: creation, TTL config, expiration checks
"""

from __future__ import annotations

import time

import pytest

from trpc_agent_sdk.sessions._types import SessionServiceConfig
from trpc_agent_sdk.types import Ttl


class TestSessionServiceConfigDefaults:
    """Test default values of SessionServiceConfig."""

    def test_default_max_events(self):
        config = SessionServiceConfig()
        assert config.max_events == 0

    def test_default_event_ttl_seconds(self):
        config = SessionServiceConfig()
        assert config.event_ttl_seconds == 0.0

    def test_default_num_recent_events(self):
        config = SessionServiceConfig()
        assert config.num_recent_events == 0

    def test_default_ttl_is_created(self):
        config = SessionServiceConfig()
        assert config.ttl is not None
        assert isinstance(config.ttl, Ttl)

    def test_custom_values(self):
        config = SessionServiceConfig(max_events=100, event_ttl_seconds=60.0, num_recent_events=20)
        assert config.max_events == 100
        assert config.event_ttl_seconds == 60.0
        assert config.num_recent_events == 20


class TestSessionServiceConfigCreateTtlConfig:
    """Test create_ttl_config static method."""

    def test_create_ttl_config_defaults(self):
        ttl = SessionServiceConfig.create_ttl_config()
        assert ttl.enable is True
        assert ttl.ttl_seconds > 0
        assert ttl.cleanup_interval_seconds > 0
        assert ttl.update_time > 0

    def test_create_ttl_config_custom(self):
        ttl = SessionServiceConfig.create_ttl_config(enable=True, ttl_seconds=3600, cleanup_interval_seconds=120.0)
        assert ttl.enable is True
        assert ttl.ttl_seconds == 3600
        assert ttl.cleanup_interval_seconds == 120.0

    def test_create_ttl_config_disabled(self):
        ttl = SessionServiceConfig.create_ttl_config(enable=False, ttl_seconds=0, cleanup_interval_seconds=0.0)
        assert ttl.enable is False
        assert ttl.ttl_seconds == 0


class TestSessionServiceConfigCleanTtl:
    """Test clean_ttl_config method."""

    def test_clean_ttl_config(self):
        config = SessionServiceConfig()
        config.ttl = SessionServiceConfig.create_ttl_config(enable=True, ttl_seconds=3600, cleanup_interval_seconds=60.0)
        config.clean_ttl_config()
        assert config.ttl.enable is False
        assert config.ttl.ttl_seconds == 0
        assert config.ttl.cleanup_interval_seconds == 0.0
        assert config.ttl.update_time == 0.0


class TestSessionServiceConfigNeedTtlExpire:
    """Test need_ttl_expire method."""

    def test_need_ttl_expire_enabled(self):
        config = SessionServiceConfig()
        config.ttl = SessionServiceConfig.create_ttl_config(enable=True, ttl_seconds=3600, cleanup_interval_seconds=60.0)
        assert config.need_ttl_expire() is True

    def test_need_ttl_expire_disabled(self):
        config = SessionServiceConfig()
        config.clean_ttl_config()
        assert config.need_ttl_expire() is False

    def test_need_ttl_expire_zero_ttl(self):
        config = SessionServiceConfig()
        config.ttl = SessionServiceConfig.create_ttl_config(enable=True, ttl_seconds=0, cleanup_interval_seconds=60.0)
        assert config.need_ttl_expire() is False


class TestSessionServiceConfigIsExpiredByTimestamp:
    """Test is_expired_by_timestamp method."""

    def test_not_expired(self):
        config = SessionServiceConfig()
        config.ttl = SessionServiceConfig.create_ttl_config(enable=True, ttl_seconds=3600, cleanup_interval_seconds=60.0)
        assert config.is_expired_by_timestamp(time.time()) is False

    def test_expired(self):
        config = SessionServiceConfig()
        config.ttl = SessionServiceConfig.create_ttl_config(enable=True, ttl_seconds=10, cleanup_interval_seconds=60.0)
        old_timestamp = time.time() - 100
        assert config.is_expired_by_timestamp(old_timestamp) is True

    def test_not_expired_when_disabled(self):
        config = SessionServiceConfig()
        config.clean_ttl_config()
        old_timestamp = time.time() - 100000
        assert config.is_expired_by_timestamp(old_timestamp) is False
