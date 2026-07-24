# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Model configuration for the code review agent.

Reads model settings from environment variables with sensible defaults.
"""

from __future__ import annotations

import os
from typing import Optional


def get_model_config() -> tuple[str, str, str]:
    """Get model configuration from environment variables.

    Returns:
        Tuple of (api_key, base_url, model_name).
    """
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "deepseek-chat")
    return api_key, base_url, model_name