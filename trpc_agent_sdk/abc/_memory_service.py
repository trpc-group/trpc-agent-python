# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Base memory service interface and implementations."""

from __future__ import annotations

import time
from abc import ABC
from abc import abstractmethod
from typing import Optional
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic import Field

if TYPE_CHECKING:
    from trpc_agent_sdk.context import AgentContext

from ._session import SessionABC
from ..types import Ttl
from ..types import SearchMemoryResponse
from ..types import DEFAULT_TTL_SECONDS
from ..types import DEFAULT_CLEANUP_INTERVAL_SECONDS


class MemoryServiceConfig(BaseModel):
    """Memory service configuration."""
    enabled: bool = Field(default=False, description="Whether the memory service is enabled")
    """Whether the memory service is enabled. If False, the memory service will not store any data."""
    ttl: Ttl = Field(default_factory=Ttl)
    """TTL configuration for the memory service."""

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


class MemoryServiceABC(ABC):
    """Abstract base class for memory/RAG services."""

    def __init__(self, memory_service_config: Optional[MemoryServiceConfig] = None, enabled: bool = False) -> None:
        if memory_service_config is None:
            memory_service_config = MemoryServiceConfig(enabled=enabled)
            memory_service_config.clean_ttl_config()
        self._memory_service_config = memory_service_config

    @property
    def enabled(self) -> bool:
        return self._memory_service_config.enabled

    @abstractmethod
    async def store_session(self, session: SessionABC, agent_context: Optional["AgentContext"] = None) -> None:
        """Store content in memory for future retrieval.

        Args:
            session: The session to store in memory.
            agent_context: The agent context for user interaction control.
        """

    @abstractmethod
    async def search_memory(self,
                            key: str,
                            query: str,
                            limit: int = 10,
                            agent_context: Optional["AgentContext"] = None) -> SearchMemoryResponse:
        """Search memory for relevant content.

        Args:
            key: The session id to search memory for.
            query: The query to search memory for.
            limit: The maximum number of results to return.
            agent_context: The agent context for user interaction control.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the memory service."""
