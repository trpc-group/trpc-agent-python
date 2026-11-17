# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Guarded imports for the optional DeepSeek Harness SDK dependency."""

try:
    from deepseek_harness import HarnessClient
    from deepseek_harness import HarnessConfig
    from deepseek_harness import InitializeResponse
    from deepseek_harness import JsonObject
    from deepseek_harness import Notification
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        'DSHAgent requires the DSH Python SDK. Install it with: pip install "trpc-agent-py[dsh]"'
    ) from exc

__all__ = [
    "HarnessClient",
    "HarnessConfig",
    "InitializeResponse",
    "JsonObject",
    "Notification",
]
