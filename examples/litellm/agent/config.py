# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Read API key, base_url, model_name (provider/model) from env."""

import os

MODEL_NAME = "openai/glm-4.7"


def get_model_config() -> tuple[str, str, str]:
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    if not api_key:
        raise ValueError("TRPC_AGENT_API_KEY must be set")
    return api_key, base_url, MODEL_NAME
