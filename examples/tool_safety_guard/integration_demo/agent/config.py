# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Model configuration from environment variables."""

from __future__ import annotations

import os


def get_model_config() -> tuple[str, str, str]:
    """Return OpenAI-compatible (api_key, base_url, model_name) from env vars.

    Set these before running the demo:
      export TRPC_AGENT_API_KEY=your-api-key
      export TRPC_AGENT_BASE_URL=https://api.openai.com/v1
      export TRPC_AGENT_MODEL_NAME=gpt-4o
    """
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
        raise RuntimeError(
            f"Missing model environment variables: {joined}. "
            f"Set them before running the demo."
        )
    return api_key, base_url, model_name
