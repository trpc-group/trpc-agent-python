# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration fixtures for replay consistency tests."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.sessions._types import SessionServiceConfig


def make_session_config(config_data: dict[str, Any] | None = None) -> SessionServiceConfig:
    """Create a SessionServiceConfig from dict."""
    config = SessionServiceConfig(**(config_data or {}))
    config.clean_ttl_config()
    return config


def make_memory_config(config_data: dict[str, Any] | None = None) -> MemoryServiceConfig:
    """Create a MemoryServiceConfig from dict."""
    config = MemoryServiceConfig(**(config_data or {}))
    config.clean_ttl_config()
    return config
