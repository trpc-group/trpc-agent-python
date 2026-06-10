# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for CachePerformanceAnalyzer and analyze_cache_performance."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.events import CacheMetrics
from trpc_agent_sdk.events import analyze_cache_performance
from trpc_agent_sdk.events._event import Event
from trpc_agent_sdk.types import GenerateContentResponseUsageMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_with_usage(
    prompt: int,
    candidates: int,
    cache_read: int | None = None,
    cache_creation: int | None = None,
) -> Event:
    """Build an Event with the given usage metadata values."""
    usage = GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        total_token_count=prompt + candidates,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )
    return Event(invocation_id="test-inv", author="model", usage_metadata=usage)


def _event_without_usage() -> Event:
    """Build an Event without usage metadata."""
    return Event(invocation_id="test-inv", author="model")


# ---------------------------------------------------------------------------
# CacheMetrics default state
# ---------------------------------------------------------------------------


class TestCacheMetricsDefaults:

    def test_all_zero_by_default(self):
        m = CacheMetrics()
        assert m.total_requests == 0
        assert m.requests_with_cache_hits == 0
        assert m.total_prompt_tokens == 0
        assert m.total_cache_read_tokens == 0
        assert m.total_cache_creation_tokens == 0
        assert m.cache_hit_ratio == 0.0
        assert m.cache_utilization_ratio == 0.0
        assert m.avg_cached_tokens_per_request == 0.0


# ---------------------------------------------------------------------------
# analyze_cache_performance — mirrors Rust test cases
# ---------------------------------------------------------------------------


class TestAnalyzeCachePerformance:

    def test_empty_events(self):
        metrics = analyze_cache_performance([])
        assert metrics.total_requests == 0
        assert metrics.requests_with_cache_hits == 0
        assert metrics.total_prompt_tokens == 0
        assert metrics.total_cache_read_tokens == 0
        assert metrics.total_cache_creation_tokens == 0
        assert metrics.cache_hit_ratio == 0.0
        assert metrics.cache_utilization_ratio == 0.0
        assert metrics.avg_cached_tokens_per_request == 0.0

    def test_events_without_usage_metadata(self):
        events = [_event_without_usage(), _event_without_usage()]
        metrics = analyze_cache_performance(events)
        assert metrics.total_requests == 0
        assert metrics.cache_hit_ratio == 0.0

    def test_single_event_no_cache(self):
        events = [_event_with_usage(1000, 200, None, None)]
        metrics = analyze_cache_performance(events)
        assert metrics.total_requests == 1
        assert metrics.requests_with_cache_hits == 0
        assert metrics.total_prompt_tokens == 1000
        assert metrics.total_cache_read_tokens == 0
        assert metrics.total_cache_creation_tokens == 0
        assert metrics.cache_hit_ratio == 0.0
        assert metrics.cache_utilization_ratio == 0.0
        assert metrics.avg_cached_tokens_per_request == 0.0

    def test_single_event_with_cache_hit(self):
        events = [_event_with_usage(1000, 200, cache_read=500)]
        metrics = analyze_cache_performance(events)
        assert metrics.total_requests == 1
        assert metrics.requests_with_cache_hits == 1
        assert metrics.total_prompt_tokens == 1000
        assert metrics.total_cache_read_tokens == 500
        assert metrics.cache_hit_ratio == pytest.approx(50.0)
        assert metrics.cache_utilization_ratio == pytest.approx(100.0)
        assert metrics.avg_cached_tokens_per_request == pytest.approx(500.0)

    def test_mixed_events(self):
        events = [
            _event_with_usage(1000, 200, cache_read=800, cache_creation=200),
            _event_with_usage(1000, 300, None, None),
            _event_with_usage(1000, 100, cache_read=600),
            _event_without_usage(),
        ]
        metrics = analyze_cache_performance(events)
        assert metrics.total_requests == 3
        assert metrics.requests_with_cache_hits == 2
        assert metrics.total_prompt_tokens == 3000
        assert metrics.total_cache_read_tokens == 1400
        assert metrics.total_cache_creation_tokens == 200
        assert metrics.cache_hit_ratio == pytest.approx(1400 / 3000 * 100)
        assert metrics.cache_utilization_ratio == pytest.approx(2 / 3 * 100)
        assert metrics.avg_cached_tokens_per_request == pytest.approx(1400 / 3)

    def test_all_cache_hits(self):
        events = [
            _event_with_usage(500, 100, cache_read=500),
            _event_with_usage(500, 100, cache_read=500),
        ]
        metrics = analyze_cache_performance(events)
        assert metrics.total_requests == 2
        assert metrics.requests_with_cache_hits == 2
        assert metrics.cache_hit_ratio == pytest.approx(100.0)
        assert metrics.cache_utilization_ratio == pytest.approx(100.0)
        assert metrics.avg_cached_tokens_per_request == pytest.approx(500.0)

    def test_zero_prompt_tokens_no_division_by_zero(self):
        events = [_event_with_usage(0, 100, None, None)]
        metrics = analyze_cache_performance(events)
        assert metrics.total_requests == 1
        assert metrics.total_prompt_tokens == 0
        assert metrics.cache_hit_ratio == 0.0
        assert metrics.cache_utilization_ratio == 0.0

    def test_cache_creation_only(self):
        events = [_event_with_usage(2000, 500, None, cache_creation=1500)]
        metrics = analyze_cache_performance(events)
        assert metrics.total_requests == 1
        assert metrics.requests_with_cache_hits == 0
        assert metrics.total_cache_creation_tokens == 1500
        assert metrics.cache_hit_ratio == 0.0
        assert metrics.cache_utilization_ratio == 0.0

    def test_accepts_any_iterable(self):
        """Should work with generators, not just lists."""

        def gen():
            yield _event_with_usage(100, 50, cache_read=100)

        metrics = analyze_cache_performance(gen())
        assert metrics.total_requests == 1
        assert metrics.cache_hit_ratio == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Public export sanity check
# ---------------------------------------------------------------------------


class TestPublicExports:

    def test_imports_from_events_package(self):
        from trpc_agent_sdk.events import CacheMetrics as _M
        from trpc_agent_sdk.events import analyze_cache_performance as _F
        assert _M is CacheMetrics
        assert _F is analyze_cache_performance
