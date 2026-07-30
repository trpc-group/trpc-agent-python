# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""TTL types."""

from __future__ import annotations

import time
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

DEFAULT_TTL_SECONDS: int = 24 * 60 * 60
DEFAULT_CLEANUP_INTERVAL_SECONDS: float = 60.0 * 60.0


class Ttl(BaseModel):
    """TTL wrapper."""
    enable: bool = Field(default=True, description="Whether to enable TTL")
    """Whether to enable TTL. If False, no TTL is applied."""
    ttl_seconds: int = Field(default=DEFAULT_TTL_SECONDS, description="TTL in seconds")
    """Time-to-live in seconds. If 0, data never expires."""
    update_time: float = Field(default=0.0, description="Update time")
    """Update time. If 0, no update time is applied."""
    cleanup_interval_seconds: float = Field(default=DEFAULT_CLEANUP_INTERVAL_SECONDS,
                                            description="Cleanup interval in seconds")
    """Cleanup interval in seconds. If 0, cleanup is disabled."""

    def model_post_init(self, __context: Any) -> None:
        """Post init hook for the TTL wrapper."""
        if not self.need_ttl_expire():
            self.update_time = 0.0
            return
        self.update_time = time.time()

    def need_ttl_expire(self) -> bool:
        """Check if TTL expire is needed."""
        return all([self.enable, self.ttl_seconds > 0, self.cleanup_interval_seconds > 0])

    def clean_ttl_config(self) -> None:
        """Clean up the TTL configuration."""
        self.enable = False
        self.ttl_seconds = 0
        self.cleanup_interval_seconds = 0.0
        self.update_time = 0.0

    def update_expired_at(self) -> None:
        """Calculate the expired time."""
        if not self.need_ttl_expire():
            self.update_time = 0.0
            return
        self.update_time = time.time()

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Check if the TTL is expired."""
        if now is None:
            now = time.time()
        if not self.need_ttl_expire():
            return False
        return now - self.update_time > self.ttl_seconds

    def is_expired_by_timestamp(self, timestamp: float, now: Optional[float] = None) -> bool:
        """Check if the TTL is expired by timestamp."""
        if not self.need_ttl_expire():
            return False
        if now is None:
            now = time.time()
        return timestamp < now - float(self.ttl_seconds)
