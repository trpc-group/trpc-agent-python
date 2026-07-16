# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Model configuration from environment variables."""
import os


def get_model_config() -> tuple[str, str, str]:
    """Return (api_key, base_url, model_name) or raise when unset."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not url or not model_name:
        raise ValueError("TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL and "
                         "TRPC_AGENT_MODEL_NAME must be set for real-model mode")
    return api_key, url, model_name


def is_dry_run(explicit: bool) -> bool:
    """Dry-run when requested explicitly or when no API key is configured."""
    return explicit or not os.getenv("TRPC_AGENT_API_KEY", "")
