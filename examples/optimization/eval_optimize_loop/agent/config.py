# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Model config from environment variables."""

import os


def get_model_config() -> tuple[str, str, str]:
    """Return (api_key, base_url, model_name). Raises ValueError if any missing."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise ValueError(
            "TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME "
            "must all be set in environment."
        )
    return api_key, base_url, model_name
