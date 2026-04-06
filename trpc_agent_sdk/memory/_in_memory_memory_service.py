# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Directly reuse the types from adk-python
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""An in-memory memory service for prototyping purpose only."""

from __future__ import annotations

import asyncio
import time
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field

from trpc_agent_sdk.abc import MemoryEntry
from trpc_agent_sdk.abc import MemoryServiceABC as BaseMemoryService
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.abc import SearchMemoryResponse
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Ttl

from ._utils import extract_words_lower
from ._utils import format_timestamp


class EventTtl(BaseModel):
    """Event TTL."""
    event: Event = Field(..., description="Event")
    """Event"""
    ttl: Ttl = Field(default_factory=Ttl)
    """TTL configuration for the event."""

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Check if the TTL is expired."""
        return self.ttl.is_expired(now)

    def update_expired_at(self) -> None:
        """Calculate the expired time."""
        self.ttl.update_expired_at()


class InMemoryMemoryService(BaseMemoryService):
    """An in-memory memory service for prototyping purpose only.

    This service stores events in memory with optional TTL and automatic cleanup.
    It is suitable for development and testing, or for production with proper
    TTL configuration to prevent memory growth.
    Uses keyword matching instead of semantic search.

    Key features:
    - Event TTL support for automatic expiration
    - Periodic cleanup of expired events
    - TTL is checked on access (search_memory) and storage (store_session)
    """

    def __init__(self, memory_service_config: Optional[MemoryServiceConfig] = None, enabled: bool = False):
        super().__init__(memory_service_config=memory_service_config, enabled=enabled)
        self._session_events: dict[str, dict[str, list[EventTtl]]] = {}
        """Keys are app_name/user_id, session_id. Values are session event lists."""
        # Cleanup task
        self.__cleanup_task: Optional[asyncio.Task] = None
        self.__cleanup_stop_event: Optional[asyncio.Event] = None
        # Start cleanup task if enabled
        self._start_cleanup_task()

    @override
    async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
        if not isinstance(session, Session):
            raise TypeError(f"Content must be a Session, got {type(session)}")

        self._session_events[session.save_key] = self._session_events.get(session.save_key, {})
        self._session_events[session.save_key][session.id] = [
            EventTtl(event=event, ttl=self._memory_service_config.ttl) for event in session.events
            if event.content and event.content.parts
        ]

    @override
    async def search_memory(self,
                            key: str,
                            query: str,
                            limit: int = 10,
                            agent_context: Optional[AgentContext] = None) -> SearchMemoryResponse:
        if key not in self._session_events:
            return SearchMemoryResponse()

        words_in_query = extract_words_lower(query)
        response = SearchMemoryResponse()
        count = 0
        for session_events in self._session_events[key].values():
            for event_ttl in session_events:
                if not event_ttl.event.content or not event_ttl.event.content.parts:
                    continue
                words_in_event = extract_words_lower(' '.join(
                    [part.text for part in event_ttl.event.content.parts if part.text]))
                if not words_in_event:
                    continue

                if any(query_word in words_in_event for query_word in words_in_query):
                    event_ttl.update_expired_at()
                    response.memories.append(
                        MemoryEntry(
                            content=event_ttl.event.content,
                            author=event_ttl.event.author,
                            timestamp=format_timestamp(event_ttl.event.timestamp),
                        ))
                    count += 1
                    if limit > 0 and count >= limit:
                        return response
        return response

    @override
    async def close(self) -> None:
        self._stop_cleanup_task()
        await super().close()

    def _cleanup_expired(self) -> None:
        """Remove all expired events.

        """
        removed_events: dict[str, dict[str, list[EventTtl]]] = {}
        now = time.time()
        # Clean expired events
        for key, events in self._session_events.items():
            for session_id, event_list in events.items():
                for event_ttl in event_list:
                    if event_ttl.is_expired(now):
                        if key not in removed_events:
                            removed_events[key] = {}
                        if session_id not in removed_events[key]:
                            removed_events[key][session_id] = []
                        removed_events[key][session_id].append(event_ttl)
                        # self._session_events[key][session_id].remove(event_ttl)
                        logger.info("Cleaned up expired event: %s/%s/%s", key, session_id, event_ttl.event.id)
        for key, events in removed_events.items():
            for session_id, event_list in events.items():
                for event_ttl in event_list:
                    self._session_events[key][session_id].remove(event_ttl)
                if not self._session_events[key][session_id]:
                    del self._session_events[key][session_id]
            if not self._session_events[key]:
                del self._session_events[key]

    async def _cleanup_loop(self) -> None:
        """Background task for periodic cleanup of expired events."""
        logger.debug("Cleanup task started with interval: %ss",
                     self._memory_service_config.ttl.cleanup_interval_seconds)

        try:
            while not self.__cleanup_stop_event.is_set():
                try:
                    # Wait for the cleanup interval or stop event
                    await asyncio.wait_for(self.__cleanup_stop_event.wait(),
                                           timeout=self._memory_service_config.ttl.cleanup_interval_seconds)
                    # If we get here, stop event was set
                    break
                except asyncio.TimeoutError:
                    # Timeout means it's time to cleanup
                    try:
                        self._cleanup_expired()
                        logger.debug("Cleanup cycle completed")
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.error("Error during cleanup: %s", ex, exc_info=True)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Cleanup loop encountered error: %s", ex, exc_info=True)
        finally:
            logger.debug("Cleanup task stopped")

    def _start_cleanup_task(self) -> None:
        """Start the background cleanup task."""
        if not self._memory_service_config.ttl.need_ttl_expire():
            logger.debug("Cleanup task disabled (ttl is disabled)")
            return
        if self.__cleanup_task is not None:
            logger.debug("Cleanup task is already running")
            return

        self.__cleanup_stop_event = asyncio.Event()
        self.__cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("Cleanup task created")

    def _stop_cleanup_task(self) -> None:
        """Stop the background cleanup task."""
        if self.__cleanup_task is None:
            return
        if self.__cleanup_stop_event is not None:
            self.__cleanup_stop_event.set()
        if self.__cleanup_task and not self.__cleanup_task.done():
            self.__cleanup_task.cancel()
        self.__cleanup_task = None
        self.__cleanup_stop_event = None
        logger.debug("Cleanup task stopped")
