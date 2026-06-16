# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration helpers for the model retry example."""

import os

from trpc_agent_sdk.configs import ExponentialBackoffConfig
from trpc_agent_sdk.configs import ModelRetryConfig


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def get_model_config() -> tuple[str, str, str]:
    """Get model connection settings from environment variables."""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise ValueError("TRPC_AGENT_API_KEY, TRPC_AGENT_BASE_URL, and "
                         "TRPC_AGENT_MODEL_NAME must be set in environment variables")
    return api_key, base_url, model_name


def get_model_retry_config() -> ModelRetryConfig:
    """Build the opt-in SDK-managed model retry configuration."""
    return ModelRetryConfig(
        num_retries=_get_int("TRPC_AGENT_MODEL_RETRY_NUM_RETRIES", 2),
        backoff=ExponentialBackoffConfig(
            initial_backoff=_get_float("TRPC_AGENT_MODEL_RETRY_INITIAL_BACKOFF", 1.0),
            max_backoff=_get_float("TRPC_AGENT_MODEL_RETRY_MAX_BACKOFF", 8.0),
            multiplier=_get_float("TRPC_AGENT_MODEL_RETRY_BACKOFF_MULTIPLIER", 2.0),
            jitter=os.getenv("TRPC_AGENT_MODEL_RETRY_JITTER", "true").strip().lower() in {"1", "true", "yes", "y", "on"},
        ),
    )
