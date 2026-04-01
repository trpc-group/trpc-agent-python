# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent config module."""

import os


def get_model_config() -> tuple[str, str, str]:
    """Get model config from environment variables."""
    api_key = (os.environ.get("TRPC_AGENT_API_KEY") or os.environ.get("API_KEY", ""))
    url = os.environ.get("TRPC_AGENT_BASE_URL", "http://v2.open.venus.woa.com/llmproxy")
    model_name = os.environ.get("TRPC_AGENT_MODEL_NAME", "glm-4.7")
    if not api_key:
        raise ValueError("TRPC_AGENT_API_KEY or API_KEY must be set in environment variables")
    return api_key, url, model_name
