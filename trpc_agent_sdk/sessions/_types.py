# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session types."""

from __future__ import annotations

import time

from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.types import DEFAULT_CLEANUP_INTERVAL_SECONDS
from trpc_agent_sdk.types import DEFAULT_TTL_SECONDS
from trpc_agent_sdk.types import Ttl


class SessionServiceConfig(BaseModel):
    """Session configuration."""
    max_events: int = Field(default=0, description="Maximum number of events to keep")
    """Maximum number of events to keep. If 0, no limit is applied."""
    event_ttl_seconds: float = Field(default=0.0, description="TTL in seconds for events")
    """Time-to-live in seconds for events. If 0, no TTL filtering is applied."""
    num_recent_events: int = Field(default=0, description="Number of recent events to keep")
    """Number of recent events to keep. If 0, no recent events are kept."""
    ttl: Ttl = Field(default_factory=Ttl, description="TTL configuration")
    """TTL configuration."""

    @staticmethod
    def create_ttl_config(enable: bool = True,
                          ttl_seconds: int = DEFAULT_TTL_SECONDS,
                          cleanup_interval_seconds: float = DEFAULT_CLEANUP_INTERVAL_SECONDS) -> Ttl:
        """Initialize from TTL configuration."""
        return Ttl(enable=enable,
                   ttl_seconds=ttl_seconds,
                   cleanup_interval_seconds=cleanup_interval_seconds,
                   update_time=time.time())

    def clean_ttl_config(self) -> None:
        """Clean up the TTL configuration."""
        self.ttl.clean_ttl_config()

    def need_ttl_expire(self) -> bool:
        """Check if TTL expire is needed."""
        return self.ttl.need_ttl_expire()

    def is_expired_by_timestamp(self, timestamp: float) -> bool:
        """Check if the TTL is expired by timestamp."""
        return self.ttl.is_expired_by_timestamp(timestamp)
