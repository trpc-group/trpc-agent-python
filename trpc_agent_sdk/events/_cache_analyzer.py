# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cache performance analyzer for TRPC Agent framework."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Iterable

if TYPE_CHECKING:
    from ._event import Event


@dataclass
class CacheMetrics:
    """Aggregate cache performance metrics computed from a sequence of events.

    All ratio fields are percentages in the range ``[0.0, 100.0]``.
    When no events carry usage metadata, all fields are zero.

    Attributes:
        total_requests: Events that carry a ``usage_metadata`` object.
        requests_with_cache_hits: Events where ``cache_read_input_tokens > 0``.
        total_prompt_tokens: Sum of all ``prompt_token_count`` values.
        total_cache_read_tokens: Sum of all ``cache_read_input_tokens`` values.
        total_cache_creation_tokens: Sum of all ``cache_creation_input_tokens`` values.
        cache_hit_ratio: ``total_cache_read_tokens / total_prompt_tokens * 100``.
        cache_utilization_ratio: ``requests_with_cache_hits / total_requests * 100``.
        avg_cached_tokens_per_request: ``total_cache_read_tokens / total_requests``.
    """

    total_requests: int = 0
    requests_with_cache_hits: int = 0
    total_prompt_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    cache_hit_ratio: float = field(default=0.0)
    cache_utilization_ratio: float = field(default=0.0)
    avg_cached_tokens_per_request: float = field(default=0.0)


def analyze_cache_performance(events: Iterable[Event]) -> CacheMetrics:
    """Compute cache performance metrics from an iterable of events.

    Events without ``usage_metadata`` are skipped. Missing cache token fields
    default to ``0``. Division by zero is avoided; ratio fields stay ``0.0``
    when the denominator would be zero.

    Args:
        events: Any iterable of :class:`~trpc_agent_sdk.events.Event` objects,
            e.g. ``session.events`` or the list collected from an agent run.

    Returns:
        A :class:`CacheMetrics` snapshot.

    Example::

        from trpc_agent_sdk.events import analyze_cache_performance

        metrics = analyze_cache_performance(session.events)
        print(f"Cache hit ratio: {metrics.cache_hit_ratio:.1f}%")
    """
    metrics = CacheMetrics()

    for event in events:
        usage = event.usage_metadata
        if usage is None:
            continue

        metrics.total_requests += 1
        metrics.total_prompt_tokens += usage.prompt_token_count or 0

        cache_read = usage.cache_read_input_tokens or 0
        metrics.total_cache_read_tokens += cache_read
        if cache_read > 0:
            metrics.requests_with_cache_hits += 1

        cache_creation = usage.cache_creation_input_tokens or 0
        metrics.total_cache_creation_tokens += cache_creation

    if metrics.total_prompt_tokens > 0:
        metrics.cache_hit_ratio = metrics.total_cache_read_tokens / metrics.total_prompt_tokens * 100.0
    if metrics.total_requests > 0:
        metrics.cache_utilization_ratio = metrics.requests_with_cache_hits / metrics.total_requests * 100.0
        metrics.avg_cached_tokens_per_request = metrics.total_cache_read_tokens / metrics.total_requests

    return metrics
