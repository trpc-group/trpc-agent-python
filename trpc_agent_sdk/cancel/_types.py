# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared types for the cancel and steering control plane."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class SessionKey:
    """Immutable key that identifies the active run of a session."""

    app_name: str
    user_id: str
    session_id: str


class SteeringStatus(str, Enum):
    """Result of a steering request."""

    ACCEPTED = "accepted"
    NO_ACTIVE_RUN = "no_active_run"
    QUEUE_FULL = "queue_full"
    MESSAGE_TOO_LARGE = "message_too_large"
    UNSUPPORTED_CONTENT = "unsupported_content"


@dataclass(frozen=True)
class SharedMemoryConfig:
    """Capacity configuration for the shared run table."""

    max_active_runs: int = 256
    steering_queue_size: int = 4
    max_steering_bytes: int = 1024

    def __post_init__(self) -> None:
        if self.max_active_runs <= 0:
            raise ValueError("max_active_runs must be greater than 0")
        if self.steering_queue_size <= 0:
            raise ValueError("steering_queue_size must be greater than 0")
        if self.max_steering_bytes <= 0:
            raise ValueError("max_steering_bytes must be greater than 0")


__all__ = [
    "SessionKey",
    "SharedMemoryConfig",
    "SteeringStatus",
]
