# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration for the Unix socket control plane."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnixSocketConfig:
    """Configuration for the Unix socket control plane."""

    socket_path: str = "/tmp/trpc-agent-py/cancel.sock"
    poll_interval: float = 0.1
    timeout: float = 3.0


__all__ = [
    "UnixSocketConfig",
]
