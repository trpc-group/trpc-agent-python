# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Environment configuration for the online mode example."""

from __future__ import annotations

import os

REQUIRED_ENV_VARS = (
    "TRPC_AGENT_API_KEY",
    "TRPC_AGENT_BASE_URL",
    "TRPC_AGENT_MODEL_NAME",
)


def get_model_config() -> tuple[str, str, str]:
    """Return API key, base URL, and model name for online mode."""

    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise ValueError(
            "online mode requires environment variables: "
            + ", ".join(REQUIRED_ENV_VARS)
            + f"; missing: {', '.join(missing)}"
        )
    return (
        os.environ["TRPC_AGENT_API_KEY"],
        os.environ["TRPC_AGENT_BASE_URL"],
        os.environ["TRPC_AGENT_MODEL_NAME"],
    )
