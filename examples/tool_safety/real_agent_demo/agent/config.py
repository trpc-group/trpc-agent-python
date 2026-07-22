# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration helpers for the real tool-safety agent demo."""

from __future__ import annotations

import os


def get_model_config() -> tuple[str, str, str]:
    """Return OpenAI-compatible model configuration from environment variables."""
    api_key = os.environ.get("TRPC_AGENT_API_KEY", "")
    base_url = os.environ.get("TRPC_AGENT_BASE_URL", "")
    model_name = os.environ.get("TRPC_AGENT_MODEL_NAME", "")
    missing = [
        name for name, value in (
            ("TRPC_AGENT_API_KEY", api_key),
            ("TRPC_AGENT_BASE_URL", base_url),
            ("TRPC_AGENT_MODEL_NAME", model_name),
        ) if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing model environment variables: {joined}")
    return api_key, base_url, model_name
