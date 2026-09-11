# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Calculate context-token budgets with model usage as the primary source."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content

from ._config import TokenContextTrackerConfig
from ._base import BaseTokenEstimator

_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True)
class ContextTokenEstimate:
    """Describe a request's token estimate and its reliability source."""

    tokens: int
    source: str
    usage_event_id: Optional[str] = field(default=None)


@dataclass(frozen=True)
class ContextBudget:
    """Describe the model window and the request's position within it."""

    estimate: ContextTokenEstimate
    context_window_tokens: Optional[int] = field(default=None)
    effective_window_tokens: Optional[int] = field(default=None)
    warning_threshold_tokens: Optional[int] = field(default=None)
    auto_compact_threshold_tokens: Optional[int] = field(default=None)
    blocking_threshold_tokens: Optional[int] = field(default=None)

    @property
    def token_mode_enabled(self) -> bool:
        """Return whether this budget has a usable model context window."""
        return self.effective_window_tokens is not None


class HeuristicTokenEstimator(BaseTokenEstimator):
    """Estimate JSON request tokens with a mixed-language heuristic."""

    @override
    def estimate_payload_tokens(self, payload: Any) -> int:
        """Estimate CJK at one token per character and other text at four characters per token."""
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        cjk_count = len(_CJK_CHARACTER.findall(rendered))
        non_cjk_count = len(rendered) - cjk_count
        return max(1, cjk_count + math.ceil(non_cjk_count / 4))


def _content_fingerprint(content: Any) -> str:
    """Generate a stable JSON fingerprint for usage-boundary matching."""
    if hasattr(content, "model_dump"):
        payload = content.model_dump(mode="python", by_alias=True, exclude_none=True)
    else:
        payload = content
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _usage_context_tokens(usage: Any) -> int | None:
    """Extract the full context token count from normalized usage."""
    total = getattr(usage, "total_token_count", None)
    if isinstance(total, int) and total > 0:
        return total
    fields = (
        "prompt_token_count",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "candidates_token_count",
    )
    values = [getattr(usage, field, None) or 0 for field in fields]
    if not any(values):
        return None
    return sum(value for value in values if isinstance(value, int))


def _request_static_fingerprint(request: LlmRequest) -> str:
    """Extract fingerprints for model, instructions, and tool configuration."""
    config = request.config
    if config is not None:
        config_payload = config.model_dump(
            mode="python",
            by_alias=True,
            exclude_none=True,
        )
    else:
        config_payload = {}
    return json.dumps(
        {
            "model": request.model,
            "config": config_payload
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class TokenContextTracker(BaseTokenEstimator):
    """Estimate request context tokens from usage and new content."""

    def __init__(self, config: TokenContextTrackerConfig):
        """Store configuration and choose the default or injected estimator."""
        self._config = config
        self._estimator = config.estimator or HeuristicTokenEstimator()

    def _resolve_window_tokens(self, ctx: InvocationContext) -> Optional[int]:
        """Resolve the model context window from config or an application resolver."""
        if not self._config.enabled:
            return None
        explicit = self._config.model_context_window_tokens
        if explicit is not None and explicit > 0:
            return explicit
        resolver = self._config.context_window_resolver
        if resolver is None or ctx is None:
            return None
        model = ctx.agent.model if ctx.agent else None
        resolved = resolver.resolve_context_window_tokens(model)
        return resolved if resolved is not None and resolved > 0 else None

    def _estimate_request(self, request: LlmRequest) -> int:
        """Estimate tokens for a complete LlmRequest."""
        payload = request.model_dump(mode="python", by_alias=True, exclude_none=True)
        return self._estimator.estimate_payload_tokens(payload)

    def _estimate_new_contents(self, contents: list[Content]) -> int:
        """Estimate content tokens added after a usage baseline."""
        payload = [content.model_dump(mode="python", by_alias=True, exclude_none=True) for content in contents]
        return self._estimator.estimate_payload_tokens(payload) if payload else 0

    def _latest_usage_baseline(
        self,
        request: LlmRequest,
        ctx: InvocationContext,
    ) -> ContextTokenEstimate | None:
        """Match the latest usage event and estimate subsequent context."""
        session = ctx.session
        events = session.events
        if not isinstance(events, list):
            return None
        fingerprints = [_content_fingerprint(content) for content in request.contents]
        static_fingerprint = _request_static_fingerprint(request)
        for event in reversed(events):
            usage_tokens = _usage_context_tokens(getattr(event, "usage_metadata", None))
            content = getattr(event, "content", None)
            if usage_tokens is None or content is None:
                continue
            metadata = getattr(event, "custom_metadata", None) or {}
            recorded_fingerprint = metadata.get("advanced_memory_request_context_fingerprint")
            if recorded_fingerprint is not None and recorded_fingerprint != static_fingerprint:
                continue
            fingerprint = _content_fingerprint(content)
            try:
                boundary_index = len(fingerprints) - 1 - fingerprints[::-1].index(fingerprint)
            except ValueError:
                continue
            suffix_tokens = self._estimate_new_contents(request.contents[boundary_index + 1:])
            source = "usage" if suffix_tokens == 0 else "hybrid"
            return ContextTokenEstimate(
                tokens=usage_tokens + suffix_tokens,
                source=source,
                usage_event_id=getattr(event, "id", None),
            )
        return None

    def _estimate(
        self,
        request: LlmRequest,
        ctx: InvocationContext,
    ) -> ContextTokenEstimate:
        """Prefer recent model usage, falling back to a full request estimate."""
        baseline = self._latest_usage_baseline(request, ctx)
        if baseline is not None:
            return baseline
        return ContextTokenEstimate(
            tokens=self._estimate_request(request),
            source="estimated",
        )

    def estimate(
        self,
        request: LlmRequest,
        ctx: InvocationContext,
    ) -> ContextTokenEstimate:
        """Return the best available token estimate for a request."""
        return self._estimate(request, ctx)

    @override
    def estimate_payload_tokens(self, payload: Any) -> int:
        """Reuse the same estimator for non-request inputs such as session memory."""
        return self._estimator.estimate_payload_tokens(payload)

    def estimate_request_tokens(self, request: LlmRequest) -> int:
        """Estimate a complete request without applying a usage baseline."""
        return self._estimate_request(request)

    def token_mode_enabled(self, ctx: InvocationContext) -> bool:
        """Return whether the configuration resolves a model context window."""
        return self._resolve_window_tokens(ctx) is not None

    def effective_context_window_tokens(
        self,
        ctx: InvocationContext,
    ) -> int | None:
        """Return the input window after max output, or None when unknown."""
        context_window = self._resolve_window_tokens(ctx)
        if context_window is None:
            return None
        effective = context_window - getattr(self._config, "max_output_tokens", 0)
        return effective if effective > 0 else None

    @classmethod
    def record_request_context(
        cls,
        request: LlmRequest,
        ctx: InvocationContext,
    ) -> None:
        """Stage the final request fingerprint for persistence on the response Event."""
        session = ctx.session
        state = session.state
        if isinstance(state, dict):
            state["advanced_memory_pending_request_context_fingerprint"] = _request_static_fingerprint(request)

    def budget(
        self,
        request: LlmRequest,
        ctx: InvocationContext,
    ) -> ContextBudget:
        """Calculate the effective window, thresholds, and token estimate."""
        estimate = self._estimate(request, ctx)
        context_window = self._resolve_window_tokens(ctx)
        if context_window is None:
            return ContextBudget(estimate=estimate)
        max_output_tokens = self._config.max_output_tokens
        effective = context_window - max_output_tokens
        if effective <= 0:
            return ContextBudget(estimate=estimate, context_window_tokens=context_window)
        warning = math.floor(effective * self._config.warning_ratio)
        auto_compact = math.floor(effective * self._config.auto_compact_ratio)
        blocking = math.floor(effective * self._config.blocking_ratio)
        return ContextBudget(
            estimate=estimate,
            context_window_tokens=context_window,
            effective_window_tokens=effective,
            warning_threshold_tokens=warning,
            auto_compact_threshold_tokens=auto_compact,
            blocking_threshold_tokens=blocking,
        )
