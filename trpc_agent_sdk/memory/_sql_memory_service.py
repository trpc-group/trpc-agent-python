# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""An in-memory memory service for prototyping purpose only."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import List
from typing import Optional
from typing_extensions import override

from sqlalchemy import Boolean
from sqlalchemy import Text
from sqlalchemy import func
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.types import String

from trpc_agent_sdk.abc import MemoryEntry
from trpc_agent_sdk.abc import MemoryServiceABC as BaseMemoryService
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.abc import SearchMemoryResponse
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.storage import DEFAULT_MAX_KEY_LENGTH
from trpc_agent_sdk.storage import DEFAULT_MAX_VARCHAR_LENGTH
from trpc_agent_sdk.storage import DynamicJSON
from trpc_agent_sdk.storage import DynamicPickleType
from trpc_agent_sdk.storage import PreciseTimestamp
from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlStorage
from trpc_agent_sdk.storage import decode_content
from trpc_agent_sdk.storage import decode_grounding_metadata

from ._utils import extract_words_lower
from ._utils import format_timestamp


class MemStorageData(DeclarativeBase):
    """Base class for memory storage tables."""

    pass


class MemStorageEvent(MemStorageData):
    """Represents an event stored in the database."""
    __tablename__ = "mem_events"

    id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    save_key: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_KEY_LENGTH),
                                            primary_key=True,
                                            nullable=False,
                                            default="")

    invocation_id: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH))
    author: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH))
    actions: Mapped[MutableDict[str, Any]] = mapped_column(DynamicPickleType)
    long_running_tool_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    branch: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    timestamp: Mapped[PreciseTimestamp] = mapped_column(PreciseTimestamp, default=func.now())

    content: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=True)
    grounding_metadata: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=True)
    custom_metadata: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=True)

    partial: Mapped[bool] = mapped_column(Boolean, nullable=True)
    turn_complete: Mapped[bool] = mapped_column(Boolean, nullable=True)
    error_code: Mapped[str] = mapped_column(String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    error_message: Mapped[str] = mapped_column(String(1024), nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=True)

    @property
    def long_running_tool_ids(self) -> set[str]:
        return (set(json.loads(self.long_running_tool_ids_json)) if self.long_running_tool_ids_json else set())

    @long_running_tool_ids.setter
    def long_running_tool_ids(self, value: set[str]):
        if value is None:
            self.long_running_tool_ids_json = None
        else:
            self.long_running_tool_ids_json = json.dumps(list(value))

    def update_event(self, session: Session, event: Event):
        """update event from event"""
        self.id = event.id
        self.invocation_id = event.invocation_id
        self.author = event.author
        self.branch = event.branch
        self.actions = event.actions
        self.save_key = session.save_key
        self.session_id = session.id
        self.timestamp = datetime.fromtimestamp(event.timestamp)
        self.long_running_tool_ids = event.long_running_tool_ids
        self.partial = event.partial
        self.turn_complete = event.turn_complete
        self.error_code = event.error_code
        self.error_message = event.error_message
        self.interrupted = event.interrupted
        if event.content:
            self.content = event.content.model_dump(exclude_none=True, mode="json")
        if event.grounding_metadata:
            self.grounding_metadata = event.grounding_metadata.model_dump(exclude_none=True, mode="json")
        if event.custom_metadata:
            self.custom_metadata = event.custom_metadata

    @classmethod
    def from_event(cls, session: Session, event: Event) -> MemStorageEvent:
        storage_event = MemStorageEvent(
            id=event.id,
            invocation_id=event.invocation_id,
            author=event.author,
            branch=event.branch,
            actions=event.actions,
            save_key=session.save_key,
            session_id=session.id,
            timestamp=datetime.fromtimestamp(event.timestamp),
            long_running_tool_ids=event.long_running_tool_ids,
            partial=event.partial,
            turn_complete=event.turn_complete,
            error_code=event.error_code,
            error_message=event.error_message,
            interrupted=event.interrupted,
        )
        if event.content:
            storage_event.content = event.content.model_dump(exclude_none=True, mode="json")
        if event.grounding_metadata:
            storage_event.grounding_metadata = event.grounding_metadata.model_dump(exclude_none=True, mode="json")
        if event.custom_metadata:
            storage_event.custom_metadata = event.custom_metadata
        return storage_event

    def to_event(self) -> Event:
        return Event(
            id=self.id,
            invocation_id=self.invocation_id,
            author=self.author,
            branch=self.branch,
            actions=self.actions,  # type: ignore
            timestamp=self.timestamp.timestamp(),
            content=decode_content(self.content),
            long_running_tool_ids=self.long_running_tool_ids,
            partial=self.partial,
            turn_complete=self.turn_complete,
            error_code=self.error_code,
            error_message=self.error_message,
            interrupted=self.interrupted,
            grounding_metadata=decode_grounding_metadata(self.grounding_metadata),
            custom_metadata=self.custom_metadata,
        )


class SqlMemoryService(BaseMemoryService):
    """An SQL-based memory service with TTL support for automatic expiration.

    Uses keyword matching instead of semantic search.
    Stores events in SQL database with TTL support for automatic cleanup.

    Key features:
    - Event TTL support for automatic expiration
    - Periodic cleanup of expired events
    - TTL is checked on access (search_memory) and storage (store_session)
    """

    def __init__(self,
                 db_url: str,
                 is_async: bool = False,
                 enabled: bool = False,
                 memory_service_config: Optional[MemoryServiceConfig] = None,
                 **kwargs: Any):
        super().__init__(memory_service_config=memory_service_config, enabled=enabled)
        self._sql_storage = SqlStorage(is_async=is_async, db_url=db_url, metadata=MemStorageData.metadata, **kwargs)

        self.__cleanup_task: Optional[asyncio.Task] = None
        self.__cleanup_stop_event: Optional[asyncio.Event] = None

        self._start_cleanup_task()

    @override
    async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
        """Store a session in the memory.

        Only stores events that are not expired based on event_ttl_seconds.
        """
        if not isinstance(session, Session):
            raise TypeError(f"Content must be a Session, got {type(session)}")

        async with self._sql_storage.create_db_session() as sql_session:
            is_exist = False
            for event in session.events:
                if event.content and event.content.parts:
                    is_exist = True
                    # Check if the event already exists
                    event_key = SqlKey(key=(event.id, session.save_key, session.id), storage_cls=MemStorageEvent)
                    storage_event: Optional[MemStorageEvent] = await self._sql_storage.get(sql_session, event_key)
                    if storage_event:
                        storage_event.update_event(session, event)
                    else:
                        await self._sql_storage.add(sql_session, MemStorageEvent.from_event(session, event))

            if is_exist:
                await self._sql_storage.commit(sql_session)

    @override
    async def search_memory(self,
                            key: str,
                            query: str,
                            limit: int = 10,
                            agent_context: Optional[AgentContext] = None) -> SearchMemoryResponse:
        """Search the memory for a query.

        Only returns events that are not expired based on event_ttl_seconds.
        """
        words_in_query = extract_words_lower(query)
        response = SearchMemoryResponse()
        async with self._sql_storage.create_db_session() as sql_session:
            filters = [MemStorageEvent.save_key == key]
            order_func = MemStorageEvent.timestamp.desc
            conditions = SqlCondition(filters=filters, order_func=order_func, limit=limit)
            event_key = SqlKey(key=(key, ), storage_cls=MemStorageEvent)
            storage_events: List[MemStorageEvent] = await self._sql_storage.query(sql_session, event_key, conditions)
            if not storage_events:
                return response

            count = 0
            for storage_event in storage_events:
                event = storage_event.to_event()
                if not event.content or not event.content.parts:
                    continue
                words_in_event = extract_words_lower(' '.join([part.text for part in event.content.parts if part.text]))
                if not words_in_event:
                    continue
                if any(query_word in words_in_event for query_word in words_in_query):
                    count += 1
                    storage_event.timestamp = func.now()
                    response.memories.append(
                        MemoryEntry(
                            content=event.content,
                            author=event.author,
                            timestamp=format_timestamp(event.timestamp),
                        ))
            if count:
                await self._sql_storage.commit(sql_session)
        return response

    @override
    async def close(self) -> None:
        """Close the service and release resources."""
        self._stop_cleanup_task()
        if self._sql_storage:
            await self._sql_storage.close()
        await super().close()

    async def _cleanup_expired_async(self) -> None:
        """Async version of cleanup that deletes expired events from database.

        Uses SQL-level batch deletion for optimal performance.
        Deletes all expired events in a single SQL DELETE statement.
        """
        async with self._sql_storage.create_db_session() as sql_session:
            # Calculate expiration threshold using database local time
            expire_before = datetime.now() - timedelta(seconds=self._memory_service_config.ttl.ttl_seconds)

            # Count events before deletion (optional, for logging)
            count_key = SqlKey(key=tuple(), storage_cls=MemStorageEvent)
            count_filters = [MemStorageEvent.timestamp < expire_before]
            count_conditions = SqlCondition(filters=count_filters)
            expired_events = await self._sql_storage.query(sql_session, count_key, count_conditions)
            deleted_count = len(expired_events) if expired_events else 0

            if deleted_count > 0:
                # Batch delete all expired events in a single SQL statement
                delete_key = SqlKey(key=tuple(), storage_cls=MemStorageEvent)
                delete_filters = [MemStorageEvent.timestamp < expire_before]
                delete_conditions = SqlCondition(filters=delete_filters)
                await self._sql_storage.delete(sql_session, delete_key, delete_conditions)
                await self._sql_storage.commit(sql_session)
                logger.info("Memory cleanup completed: deleted %s expired events", deleted_count)

    async def _cleanup_loop(self) -> None:
        """Background task for periodic cleanup of expired events."""
        logger.debug("Memory cleanup task started with interval: %ss",
                     self._memory_service_config.ttl.cleanup_interval_seconds)

        try:
            while not self.__cleanup_stop_event.is_set():
                try:
                    await asyncio.wait_for(self.__cleanup_stop_event.wait(),
                                           timeout=self._memory_service_config.ttl.cleanup_interval_seconds)
                    break
                except asyncio.TimeoutError:
                    try:
                        await self._cleanup_expired_async()
                        logger.debug("Memory cleanup cycle completed")
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.error("Error during memory cleanup: %s", ex, exc_info=True)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Memory cleanup loop encountered error: %s", ex, exc_info=True)
        finally:
            logger.debug("Memory cleanup task stopped")

    def _start_cleanup_task(self) -> None:
        """Start the background cleanup task."""
        if not self._memory_service_config.ttl.need_ttl_expire():
            logger.debug("Memory cleanup task disabled (ttl is disabled)")
            return

        if self.__cleanup_task is not None:
            logger.debug("Memory cleanup task is already running")
            return

        self.__cleanup_stop_event = asyncio.Event()
        self.__cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("Memory cleanup task created")

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
        logger.debug("Memory cleanup task stopped")
