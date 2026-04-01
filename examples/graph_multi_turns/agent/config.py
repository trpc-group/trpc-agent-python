# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import os


def get_model_config() -> tuple[str, str, str]:
    """Get model configuration from environment variables."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "deepseek-chat")

    if not api_key:
        print("Hint: TRPC_AGENT_API_KEY is not set. Configure it if your provider requires auth.")

    return api_key, base_url, model_name
