# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent config module.

Reads model connection settings from environment variables and exposes a
helper to detect which prompt-cache "family" the resolved model belongs to.
The prompt cache knobs differ per provider, so the agent uses this to build a
``PromptCacheConfig`` that actually applies to the running model.
"""

import os


def get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables."""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')
    url = os.getenv('TRPC_AGENT_BASE_URL', '')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')
    if not api_key or not url or not model_name:
        raise ValueError('TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL, and '
                         'TRPC_AGENT_MODEL_NAME must be set in environment variables')
    return api_key, url, model_name


def is_anthropic_model(model_name: str) -> bool:
    """Return True when the model belongs to the Anthropic cache_control family.

    Anthropic (Claude) uses explicit ``cache_control`` breakpoints and a
    ``5m`` / ``1h`` TTL. Everything else is treated as the OpenAI-managed
    family, which uses ``prompt_cache_key`` and a ``in_memory`` / ``24h``
    retention instead.
    """
    bare = model_name.split('/', 1)[-1]  # strip provider prefix for LiteLLM names
    return bare.lower().startswith('claude')
