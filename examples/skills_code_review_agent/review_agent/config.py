# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration via the repository-standard TRPC_AGENT_* environment variables."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def get_model_config() -> tuple[str, str, str]:
    """Return (api_key, base_url, model_name) from the environment."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    return api_key, base_url, model_name


def has_real_model() -> bool:
    """True when a real LLM is configured (dry-run otherwise)."""
    api_key, _, model_name = get_model_config()
    return bool(api_key and model_name)
