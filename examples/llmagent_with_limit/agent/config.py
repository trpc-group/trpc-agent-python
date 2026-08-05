# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Model configuration for the run-limit example."""

import os


def get_model_config() -> tuple[str, str, str]:
    """Read the model configuration from environment variables."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise ValueError("TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL, and "
                         "TRPC_AGENT_MODEL_NAME must be set.")
    return api_key, base_url, model_name
