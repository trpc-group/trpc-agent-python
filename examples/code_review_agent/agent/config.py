"""Model configuration sourced from environment variables."""

from __future__ import annotations

import os


def get_model_config() -> tuple[str, str, str]:
    """Return API key, base URL, and model name."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "").strip()
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "").strip()
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "").strip()
    missing = [
        name for name, value in (
            ("TRPC_AGENT_API_KEY", api_key),
            ("TRPC_AGENT_BASE_URL", base_url),
            ("TRPC_AGENT_MODEL_NAME", model_name),
        ) if not value
    ]
    if missing:
        raise ValueError(f"Missing required model environment variables: {', '.join(missing)}")
    return api_key, base_url, model_name
