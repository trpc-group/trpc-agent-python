# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Agent config module"""

import os


def get_model_config() -> tuple[str, str, str]:
    """Get LLM model config from environment variables."""
    api_key = os.getenv('TRPC_AGENT_API_KEY', '')
    url = os.getenv('TRPC_AGENT_BASE_URL', '')
    model_name = os.getenv('TRPC_AGENT_MODEL_NAME', '')
    if not api_key or not url or not model_name:
        raise ValueError('''TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL,
                         and TRPC_AGENT_MODEL_NAME must be set in environment variables''')
    return api_key, url, model_name


def get_google_cse_config() -> tuple[str, str]:
    """Get Google Custom Search credentials from environment variables.
    """
    api_key = os.getenv('GOOGLE_CSE_API_KEY', '')
    engine_id = os.getenv('GOOGLE_CSE_ENGINE_ID', '')
    return api_key, engine_id


def get_http_proxy() -> str | None:
    """Return the outbound HTTP proxy URL, if any.
    """
    return os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY') or None
