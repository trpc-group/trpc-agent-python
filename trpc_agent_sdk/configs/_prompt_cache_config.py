# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompt cache configuration for TRPC Agent framework."""

from __future__ import annotations

from typing import List
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class PromptCacheConfig(BaseModel):
    """Cross-provider prompt cache configuration.

    This is a single flat config for SDK-managed prompt cache customization.
    Many providers already enable prompt caching automatically; for those
    providers this config only supplies optional hints such as cache keys,
    retention, or usage normalization. Fields are applied on a best-effort basis
    depending on the resolved provider. Fields that do not apply to a given
    provider are silently ignored (no error), so the same config "just works"
    across Anthropic, OpenAI, and the LiteLLM channel.

    Default ``enabled=False`` means the SDK does not add cache-specific request
    customization: it injects no ``cache_control`` and sends no
    ``prompt_cache_key`` / ``prompt_cache_retention``. Provider-native automatic
    prompt caching, when available, may still happen independently.
    """

    enabled: bool = False
    """Master switch for SDK-managed prompt cache customization."""

    ttl: Optional[str] = None
    """Provider-specific cache lifetime hint.

    The SDK does not validate TTL values because supported values vary across
    providers, deployments, and self-hosted OpenAI-compatible services. When set,
    the value is forwarded to the resolved provider's cache TTL field; providers
    may accept, ignore, or reject it. ``None`` means "do not send a lifetime
    hint" (provider default).
    """

    breakpoints: List[Literal["tools", "system", "messages"]] = Field(default_factory=lambda: ["system"])
    """Cache-control injection points for Anthropic-style providers.

    Used by native Anthropic and LiteLLM models routed to the Anthropic cache
    family; ignored by OpenAI-managed providers. Current injection behavior:

    - ``"tools"``: stamp the last tool with ``cache_control``. For LiteLLM
      Bedrock models this is represented as a ``tool_config`` cache point.
    - ``"system"``: stamp the system prompt/system message.
    - ``"messages"``: stamp one conversation-message breakpoint on the most
      recent assistant message, keeping the current user turn outside the cached
      prefix. LiteLLM uses its ``cache_control_injection_points`` support to
      target that assistant message by index.

    An empty list means the SDK does not add Anthropic-style cache-control
    injection points. User-authored provider-specific cache metadata is still
    forwarded when supported by the underlying model adapter.
    """

    prompt_cache_key: Optional[str] = None
    """OpenAI-managed family only.

    Improves cache-hit stability by keeping same-prefix requests sticky to the
    same backend. Only used by OpenAI-managed providers.
    """
