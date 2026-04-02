# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tests for trpc_agent_sdk.types._ttl.

Covers:
    - Module-level constants: DEFAULT_TTL_SECONDS, DEFAULT_CLEANUP_INTERVAL_SECONDS
    - Ttl: defaults, model_post_init, need_ttl_expire, clean_ttl_config,
      update_expired_at, is_expired, is_expired_by_timestamp
"""

from __future__ import annotations

import time
from unittest.mock import patch

from trpc_agent_sdk.types._ttl import (
    DEFAULT_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_TTL_SECONDS,
    Ttl,
)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------
class TestModuleConstants:
    """Verify module-level default values."""

    def test_default_ttl_seconds(self):
        assert DEFAULT_TTL_SECONDS == 24 * 60 * 60

    def test_default_cleanup_interval_seconds(self):
        assert DEFAULT_CLEANUP_INTERVAL_SECONDS == 60.0 * 60.0


# ---------------------------------------------------------------------------
# Ttl defaults
# ---------------------------------------------------------------------------
class TestTtlDefaults:
    """Default construction and field values."""

    def test_enable_default(self):
        ttl = Ttl()
        assert ttl.enable is True

    def test_ttl_seconds_default(self):
        ttl = Ttl()
        assert ttl.ttl_seconds == DEFAULT_TTL_SECONDS

    def test_cleanup_interval_default(self):
        ttl = Ttl()
        assert ttl.cleanup_interval_seconds == DEFAULT_CLEANUP_INTERVAL_SECONDS

    def test_update_time_set_by_post_init(self):
        ttl = Ttl()
        assert ttl.update_time > 0

    def test_update_time_close_to_now(self):
        before = time.time()
        ttl = Ttl()
        after = time.time()
        assert before <= ttl.update_time <= after


# ---------------------------------------------------------------------------
# model_post_init
# ---------------------------------------------------------------------------
class TestTtlModelPostInit:
    """model_post_init sets update_time correctly."""

    def test_post_init_sets_time_when_ttl_needed(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        assert ttl.update_time > 0

    def test_post_init_zeroes_time_when_disabled(self):
        ttl = Ttl(enable=False)
        assert ttl.update_time == 0.0

    def test_post_init_zeroes_time_when_ttl_zero(self):
        ttl = Ttl(enable=True, ttl_seconds=0)
        assert ttl.update_time == 0.0

    def test_post_init_zeroes_time_when_cleanup_zero(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=0.0)
        assert ttl.update_time == 0.0


# ---------------------------------------------------------------------------
# need_ttl_expire
# ---------------------------------------------------------------------------
class TestNeedTtlExpire:
    """Logic: enable AND ttl_seconds > 0 AND cleanup_interval_seconds > 0."""

    def test_all_conditions_met(self):
        ttl = Ttl(enable=True, ttl_seconds=60, cleanup_interval_seconds=30.0)
        assert ttl.need_ttl_expire() is True

    def test_disabled(self):
        ttl = Ttl(enable=False, ttl_seconds=60, cleanup_interval_seconds=30.0)
        assert ttl.need_ttl_expire() is False

    def test_ttl_zero(self):
        ttl = Ttl(enable=True, ttl_seconds=0, cleanup_interval_seconds=30.0)
        assert ttl.need_ttl_expire() is False

    def test_cleanup_zero(self):
        ttl = Ttl(enable=True, ttl_seconds=60, cleanup_interval_seconds=0.0)
        assert ttl.need_ttl_expire() is False

    def test_all_zero(self):
        ttl = Ttl(enable=False, ttl_seconds=0, cleanup_interval_seconds=0.0)
        assert ttl.need_ttl_expire() is False


# ---------------------------------------------------------------------------
# clean_ttl_config
# ---------------------------------------------------------------------------
class TestCleanTtlConfig:
    """clean_ttl_config resets all fields."""

    def test_clean(self):
        ttl = Ttl(enable=True, ttl_seconds=300, cleanup_interval_seconds=60.0)
        ttl.clean_ttl_config()
        assert ttl.enable is False
        assert ttl.ttl_seconds == 0
        assert ttl.cleanup_interval_seconds == 0.0
        assert ttl.update_time == 0.0

    def test_clean_idempotent(self):
        ttl = Ttl()
        ttl.clean_ttl_config()
        ttl.clean_ttl_config()
        assert ttl.enable is False
        assert ttl.ttl_seconds == 0


# ---------------------------------------------------------------------------
# update_expired_at
# ---------------------------------------------------------------------------
class TestUpdateExpiredAt:
    """update_expired_at refreshes update_time."""

    def test_refreshes_time(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        old_time = ttl.update_time

        with patch("trpc_agent_sdk.types._ttl.time.time", return_value=old_time + 50):
            ttl.update_expired_at()

        assert ttl.update_time == old_time + 50

    def test_zeroes_when_not_needed(self):
        ttl = Ttl(enable=False)
        ttl.update_expired_at()
        assert ttl.update_time == 0.0

    def test_zeroes_when_ttl_zero(self):
        ttl = Ttl(enable=True, ttl_seconds=0)
        ttl.update_expired_at()
        assert ttl.update_time == 0.0


# ---------------------------------------------------------------------------
# is_expired
# ---------------------------------------------------------------------------
class TestIsExpired:
    """is_expired checks current time against update_time + ttl_seconds."""

    def test_not_expired_recently_created(self):
        ttl = Ttl(enable=True, ttl_seconds=3600, cleanup_interval_seconds=60.0)
        assert ttl.is_expired() is False

    def test_expired_with_explicit_now(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        future = ttl.update_time + 200
        assert ttl.is_expired(now=future) is True

    def test_not_expired_exact_boundary(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        boundary = ttl.update_time + 100
        assert ttl.is_expired(now=boundary) is False

    def test_just_past_boundary(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        just_past = ttl.update_time + 100.001
        assert ttl.is_expired(now=just_past) is True

    def test_disabled_never_expires(self):
        ttl = Ttl(enable=False, ttl_seconds=100, cleanup_interval_seconds=10.0)
        assert ttl.is_expired(now=time.time() + 999999) is False

    def test_ttl_zero_never_expires(self):
        ttl = Ttl(enable=True, ttl_seconds=0, cleanup_interval_seconds=10.0)
        assert ttl.is_expired(now=time.time() + 999999) is False

    def test_is_expired_uses_current_time_by_default(self):
        fixed = 1000.0
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        ttl.update_time = fixed
        with patch("trpc_agent_sdk.types._ttl.time.time", return_value=fixed + 200):
            assert ttl.is_expired() is True


# ---------------------------------------------------------------------------
# is_expired_by_timestamp
# ---------------------------------------------------------------------------
class TestIsExpiredByTimestamp:
    """is_expired_by_timestamp compares an external timestamp."""

    def test_not_expired(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        now = time.time()
        assert ttl.is_expired_by_timestamp(now - 50, now=now) is False

    def test_expired(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        now = time.time()
        assert ttl.is_expired_by_timestamp(now - 200, now=now) is True

    def test_exact_boundary(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        now = 2000.0
        assert ttl.is_expired_by_timestamp(now - 100, now=now) is False

    def test_just_past_boundary(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        now = 2000.0
        assert ttl.is_expired_by_timestamp(now - 100.001, now=now) is True

    def test_disabled_never_expires(self):
        ttl = Ttl(enable=False, ttl_seconds=100, cleanup_interval_seconds=10.0)
        assert ttl.is_expired_by_timestamp(0, now=time.time()) is False

    def test_uses_current_time_by_default(self):
        ttl = Ttl(enable=True, ttl_seconds=100, cleanup_interval_seconds=10.0)
        fixed_now = 5000.0
        with patch("trpc_agent_sdk.types._ttl.time.time", return_value=fixed_now):
            assert ttl.is_expired_by_timestamp(fixed_now - 200) is True
            assert ttl.is_expired_by_timestamp(fixed_now - 50) is False
