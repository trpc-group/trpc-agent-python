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
#
"""SQL session service implementation."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import List
from typing import Optional
from typing_extensions import override

from sqlalchemy import Boolean
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy import Text
from sqlalchemy import func
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.types import Integer

from trpc_agent_sdk.abc import ListSessionsResponse
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.storage import DEFAULT_MAX_KEY_LENGTH
from trpc_agent_sdk.storage import DEFAULT_MAX_VARCHAR_LENGTH
from trpc_agent_sdk.storage import DynamicJSON
from trpc_agent_sdk.storage import DynamicPickleType
from trpc_agent_sdk.storage import PreciseTimestamp
from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlSession
from trpc_agent_sdk.storage import SqlStorage
from trpc_agent_sdk.storage import UTF8MB4String
from trpc_agent_sdk.storage import decode_content
from trpc_agent_sdk.storage import decode_grounding_metadata
from trpc_agent_sdk.storage import decode_usage_metadata
from trpc_agent_sdk.utils import user_key

from ._base_session_service import BaseSessionService
from ._session import Session
from ._summarizer_manager import SummarizerSessionManager
from ._types import SessionServiceConfig
from ._utils import StateStorageEntry
from ._utils import extract_state_delta
from ._utils import merge_state


class SessionStorageBase(DeclarativeBase):
    """Base class for SqlSessionService tables only.

    This creates a separate metadata that only includes tables needed by SqlSessionService,
    avoiding the creation of tables from other services (e.g., mem_events from SqlMemoryService).
    """
    pass


class StorageSession(SessionStorageBase):
    """Represents a session stored in the database with TTL support.

    TTL is calculated based on update_time + configured ttl_seconds.
    No need to store expired_at as we can calculate it on the fly.
    """
    __tablename__ = "sessions"

    app_name: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    id: Mapped[str] = mapped_column(
        UTF8MB4String(DEFAULT_MAX_KEY_LENGTH),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    state: Mapped[MutableDict[str, Any]] = mapped_column(MutableDict.as_mutable(DynamicJSON), default={})
    conversation_count: Mapped[int] = mapped_column(Integer, default=0)

    create_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now())
    update_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())

    storage_events: Mapped[list[SessionStorageEvent]] = relationship(
        "SessionStorageEvent",
        back_populates="storage_session",
    )

    def __repr__(self):
        return f"<StorageSession(id={self.id}, update_time={self.update_time})>"

    @property
    def _dialect_name(self) -> Optional[str]:
        session = inspect(self).session
        return session.bind.dialect.name if session else None  # type: ignore

    @property
    def update_timestamp_tz(self) -> float:
        if self._dialect_name == "sqlite":
            return self.update_time.replace(tzinfo=timezone.utc).timestamp()
        return self.update_time.timestamp()

    def to_session(
        self,
        state: dict[str, Any] | None = None,
        events: list[Event] | None = None,
    ) -> Session:
        if state is None:
            state = {}
        if events is None:
            events = []
        return Session(
            app_name=self.app_name,
            user_id=self.user_id,
            id=self.id,
            state=state,
            events=events,
            conversation_count=self.conversation_count,
            last_update_time=self.update_timestamp_tz,
            save_key=user_key(self.app_name, self.user_id),
        )


class SessionStorageEvent(SessionStorageBase):
    """Represents a session event stored in the database."""
    """Represents an event stored in the database."""
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    app_name: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)

    invocation_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))
    author: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH))
    actions: Mapped[MutableDict[str, Any]] = mapped_column(DynamicPickleType)
    long_running_tool_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    branch: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    request_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    parent_invocation_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    tag: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    filter_key: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    requires_completion: Mapped[bool] = mapped_column(Boolean, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timestamp: Mapped[PreciseTimestamp] = mapped_column(PreciseTimestamp, default=func.now())
    visible: Mapped[bool] = mapped_column(Boolean, nullable=True)
    object: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=False, default="")
    model_flags: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    partial: Mapped[bool] = mapped_column(Boolean, nullable=True)
    turn_complete: Mapped[bool] = mapped_column(Boolean, nullable=True)
    error_code: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    error_message: Mapped[str] = mapped_column(UTF8MB4String(1024), nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=True)
    response_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_VARCHAR_LENGTH), nullable=True)
    content: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=True)
    grounding_metadata: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=True)
    custom_metadata: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=True)
    usage_metadata: Mapped[dict[str, Any]] = mapped_column(DynamicJSON, nullable=True)


    storage_session: Mapped[StorageSession] = relationship(
        "StorageSession",
        back_populates="storage_events",
    )

    __table_args__ = (ForeignKeyConstraint(
        ["app_name", "user_id", "session_id"],
        ["sessions.app_name", "sessions.user_id", "sessions.id"],
        ondelete="CASCADE",
    ), )

    @property
    def long_running_tool_ids(self) -> set[str]:
        return (set(json.loads(self.long_running_tool_ids_json)) if self.long_running_tool_ids_json else set())

    @long_running_tool_ids.setter
    def long_running_tool_ids(self, value: set[str]):
        if value is None:
            self.long_running_tool_ids_json = None
        else:
            self.long_running_tool_ids_json = json.dumps(list(value))

    @classmethod
    def from_event(cls, session: Session, event: Event) -> SessionStorageEvent:
        storage_event = SessionStorageEvent(
            id=event.id,
            app_name=session.app_name,
            user_id=session.user_id,
            session_id=session.id,
            invocation_id=event.invocation_id,
            author=event.author,
            actions=event.actions,
            long_running_tool_ids=event.long_running_tool_ids,
            branch=event.branch,
            request_id=event.request_id,
            parent_invocation_id=event.parent_invocation_id,
            tag=event.tag,
            filter_key=event.filter_key,
            requires_completion=event.requires_completion,
            version=event.version,
            timestamp=datetime.fromtimestamp(event.timestamp),
            visible=event.visible,
            object=event.object,
            model_flags=event.model_flags,
            partial=event.partial,
            turn_complete=event.turn_complete,
            error_code=event.error_code,
            error_message=event.error_message,
            interrupted=event.interrupted,
            response_id=event.response_id,
        )
        if event.content:
            storage_event.content = event.content.model_dump(exclude_none=True, mode="json")
        if event.grounding_metadata:
            storage_event.grounding_metadata = event.grounding_metadata.model_dump(exclude_none=True, mode="json")
        if event.custom_metadata:
            storage_event.custom_metadata = event.custom_metadata
        if event.usage_metadata:
            storage_event.usage_metadata = event.usage_metadata.model_dump(exclude_none=True, mode="json")
        return storage_event

    def to_event(self) -> Event:
        return Event(
            id=self.id,
            invocation_id=self.invocation_id,
            author=self.author,
            actions=self.actions,  # type: ignore
            long_running_tool_ids=self.long_running_tool_ids,
            branch=self.branch,
            request_id=self.request_id,
            parent_invocation_id=self.parent_invocation_id,
            tag=self.tag,
            filter_key=self.filter_key,
            requires_completion=self.requires_completion,
            version=self.version,
            visible=self.visible,
            object=self.object,
            model_flags=self.model_flags,
            timestamp=self.timestamp.timestamp(),
            partial=self.partial,
            turn_complete=self.turn_complete,
            error_code=self.error_code,
            error_message=self.error_message,
            interrupted=self.interrupted,
            response_id=self.response_id,
            content=decode_content(self.content),
            grounding_metadata=decode_grounding_metadata(self.grounding_metadata),
            custom_metadata=self.custom_metadata,
            usage_metadata=decode_usage_metadata(self.usage_metadata),
        )


class StorageAppState(SessionStorageBase):
    """Represents an app state stored in the database with TTL support.

    TTL is calculated based on update_time + configured app_state_ttl_seconds.
    """
    __tablename__ = "app_states"

    app_name: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    state: Mapped[MutableDict[str, Any]] = mapped_column(MutableDict.as_mutable(DynamicJSON), default={})
    update_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())


class StorageUserState(SessionStorageBase):
    """Represents a user state stored in the database with TTL support.

    TTL is calculated based on update_time + configured user_state_ttl_seconds.
    """
    __tablename__ = "user_states"

    app_name: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(UTF8MB4String(DEFAULT_MAX_KEY_LENGTH), primary_key=True)
    state: Mapped[MutableDict[str, Any]] = mapped_column(MutableDict.as_mutable(DynamicJSON), default={})
    update_time: Mapped[datetime] = mapped_column(PreciseTimestamp, default=func.now(), onupdate=func.now())


class SqlSessionService(BaseSessionService):
    """A SQL database implementation of the session service.

    This service stores sessions in a SQL database with TTL support for automatic expiration.
    It provides the same functionality as InMemorySessionService but with persistence
    and distributed access capabilities.

    Key features:
    - Session, app state, and user state TTL support
    - Session TTL is refreshed on access (get_session) and update (append_event)
    - App state and user state TTL are refreshed on access (get) and update (append_event)
    - Separation of app-scoped, user-scoped, and session-scoped state
    - Event filtering by TTL and max count

    TTL behavior matches InMemorySessionService:
    - Session: TTL refreshed on access and update
    - App State: TTL refreshed on access and update
    - User State: TTL refreshed on access and update

    TTL implementation:
    - Uses database update_time column + configured ttl_seconds to check expiration
    - Refreshes TTL by updating the update_time (triggers SQLAlchemy's onupdate=func.now())
    """

    def __init__(self,
                 db_url: str,
                 summarizer_manager: Optional[SummarizerSessionManager] = None,
                 is_async: bool = False,
                 session_config: Optional[SessionServiceConfig] = None,
                 **kwargs: Any):
        super().__init__(summarizer_manager=summarizer_manager, session_config=session_config)
        self._sql_storage = SqlStorage(is_async=is_async, db_url=db_url, metadata=SessionStorageBase.metadata, **kwargs)
        self.__cleanup_task: Optional[asyncio.Task] = None
        self.__cleanup_stop_event: Optional[asyncio.Event] = None

        self._start_cleanup_task()

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_context: Optional[AgentContext] = None,
    ) -> Session:
        session_id = session_id.strip() if session_id and session_id.strip() else str(uuid.uuid4())
        state_deltas = extract_state_delta(state)

        async with self._sql_storage.create_db_session() as sql_session:
            app_state = await self._update_app_state(sql_session, app_name, state_deltas.app_state_delta)
            user_state = await self._update_user_state(sql_session, app_name, user_id, state_deltas.user_state_delta)

            # Check if session already exists
            session_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=StorageSession)
            storage_session: Optional[StorageSession] = await self._sql_storage.get(sql_session, session_key)

            if storage_session is None:
                # Create new session
                storage_session = StorageSession(
                    app_name=app_name,
                    user_id=user_id,
                    id=session_id,
                    state=state_deltas.session_state,
                )
                await self._sql_storage.add(sql_session, storage_session)
            else:
                # Update existing session state
                storage_session.state.update(state_deltas.session_state)

            await self._sql_storage.commit(sql_session)
            await self._sql_storage.refresh(sql_session, storage_session)

            merged_state = merge_state(
                StateStorageEntry(app_state_delta=app_state,
                                  user_state_delta=user_state,
                                  session_state=state_deltas.session_state))
            return storage_session.to_session(state=merged_state)

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: Optional[AgentContext] = None,
    ) -> Optional[Session]:
        async with self._sql_storage.create_db_session() as sql_session:
            storage_session = await self._get_session(sql_session, app_name, user_id, session_id)
            if storage_session is None:
                return None

            filters = [
                SessionStorageEvent.app_name == app_name, SessionStorageEvent.session_id == session_id,
                SessionStorageEvent.user_id == user_id
            ]
            order_func = SessionStorageEvent.timestamp.desc
            conditions = SqlCondition(filters=filters,
                                      order_func=order_func,
                                      limit=self._session_config.num_recent_events or None)
            event_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=SessionStorageEvent)
            storage_events: List[SessionStorageEvent] = await self._sql_storage.query(
                sql_session, event_key, conditions)

            app_state = await self._get_app_state(sql_session, app_name)
            user_state = await self._get_user_state(sql_session, app_name, user_id)

            merged_state = merge_state(
                StateStorageEntry(app_state_delta=app_state,
                                  user_state_delta=user_state,
                                  session_state=storage_session.state))

            events = [e.to_event() for e in reversed(storage_events)]
            session = storage_session.to_session(state=merged_state, events=events)
            self.filter_events(session)
            return session

    @override
    async def list_sessions(self, *, app_name: str, user_id: str) -> ListSessionsResponse:
        async with self._sql_storage.create_db_session() as sql_session:
            filters = [StorageSession.app_name == app_name, StorageSession.user_id == user_id]
            conditions = SqlCondition(filters=filters)
            session_key = SqlKey(key=(app_name, user_id), storage_cls=StorageSession)
            results: List[StorageSession] = await self._sql_storage.query(sql_session, session_key, conditions)

            app_state = await self._get_app_state(sql_session, app_name)
            user_state = await self._get_user_state(sql_session, app_name, user_id)

            sessions = []
            for storage_session in results:
                if self._session_config.is_expired_by_timestamp(storage_session.update_time.timestamp()):
                    logger.debug("Cleaned up expired session: %s/%s/%s", storage_session.app_name,
                                 storage_session.user_id, storage_session.id)
                    continue

                storage_session.events = []

                merged_state = merge_state(
                    StateStorageEntry(app_state_delta=app_state,
                                      user_state_delta=user_state,
                                      session_state=storage_session.state))

                sessions.append(storage_session.to_session(state=merged_state))
            return ListSessionsResponse(sessions=sessions)

    @override
    async def delete_session(self, app_name: str, user_id: str, session_id: str) -> None:
        async with self._sql_storage.create_db_session() as sql_session:
            filters = [
                StorageSession.app_name == app_name, StorageSession.user_id == user_id, StorageSession.id == session_id
            ]
            conditions = SqlCondition(filters=filters)
            session_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=StorageSession)
            await self._sql_storage.delete(sql_session, session_key, conditions)
            await self._sql_storage.commit(sql_session)

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        if event.partial:
            return event

        await super().append_event(session=session, event=event)

        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id

        async with self._sql_storage.create_db_session() as sql_session:
            session_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=StorageSession)
            storage_session: Optional[StorageSession] = await self._sql_storage.get(sql_session, session_key)
            if not storage_session:
                logger.warning("Session %s not found in storage, it will be created", session_id)
                return event

            time_diff = storage_session.update_timestamp_tz - session.last_update_time
            if time_diff > 1.0:
                logger.warning(
                    "Session %s is stale (time diff: %ss). Reloading session from database to get latest state.",
                    session_id, time_diff)
                await self._sql_storage.refresh(sql_session, storage_session)
                filters = [
                    SessionStorageEvent.app_name == app_name, SessionStorageEvent.session_id == session_id,
                    SessionStorageEvent.user_id == user_id
                ]
                order_func = SessionStorageEvent.timestamp.desc
                conditions = SqlCondition(filters=filters, order_func=order_func)
                event_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=SessionStorageEvent)
                storage_events: List[SessionStorageEvent] = await self._sql_storage.query(
                    sql_session, event_key, conditions)
                session.last_update_time = storage_session.update_timestamp_tz
                session.state = storage_session.state
                session.conversation_count = storage_session.conversation_count
                session.events = [e.to_event() for e in reversed(storage_events)]

            storage_session.conversation_count = session.conversation_count

            if event.actions and event.actions.state_delta:
                state_entry = extract_state_delta(event.actions.state_delta)

                if state_entry.app_state_delta:
                    await self._update_app_state(sql_session, app_name, state_entry.app_state_delta)

                if state_entry.user_state_delta:
                    await self._update_user_state(sql_session, app_name, user_id, state_entry.user_state_delta)

                if state_entry.session_state:
                    session_state = storage_session.state
                    session_state.update(state_entry.session_state)
                    storage_session.state = session_state  # type: ignore

            await self._sql_storage.add(sql_session, SessionStorageEvent.from_event(session, event))
            await self._sql_storage.commit(sql_session)
            await self._sql_storage.refresh(sql_session, storage_session)

            session.last_update_time = storage_session.update_timestamp_tz

        return event

    @override
    async def update_session(self, session: Session) -> None:
        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id

        async with self._sql_storage.create_db_session() as sql_session:
            session_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=StorageSession)
            storage_session: Optional[StorageSession] = await self._sql_storage.get(sql_session, session_key)
            if storage_session is None:
                logger.warning("Session %s not found in storage, it will be created", session_id)
                return

            filters = [
                SessionStorageEvent.app_name == app_name, SessionStorageEvent.user_id == user_id,
                SessionStorageEvent.session_id == session_id
            ]
            conditions = SqlCondition(filters=filters)
            event_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=SessionStorageEvent)
            await self._sql_storage.delete(sql_session, event_key, conditions)

            for event in (session.events or []):
                await self._sql_storage.add(sql_session, SessionStorageEvent.from_event(session, event))

            storage_session.state = session.state  # type: ignore
            storage_session.conversation_count = session.conversation_count

            await self._sql_storage.commit(sql_session)
            await self._sql_storage.refresh(sql_session, storage_session)

            session.last_update_time = storage_session.update_timestamp_tz

    @override
    async def close(self) -> None:
        self._stop_cleanup_task()
        if self._sql_storage:
            await self._sql_storage.close()
        await super().close()

    async def _update_app_state(self, sql_session: SqlSession, app_name: str, state_delta: dict[str,
                                                                                                Any]) -> dict[str, Any]:
        app_key = SqlKey(key=(app_name, ), storage_cls=StorageAppState)
        storage_app_state: Optional[StorageAppState] = await self._sql_storage.get(sql_session, app_key)

        app_state = storage_app_state.state if storage_app_state else {}
        if state_delta:
            app_state.update(state_delta)

        if not storage_app_state:
            storage_app_state = StorageAppState(app_name=app_name, state=app_state)
            await self._sql_storage.add(sql_session, storage_app_state)
        else:
            storage_app_state.state = app_state  # type: ignore
        storage_app_state.update_time = func.now()

        return app_state

    async def _update_user_state(self, sql_session: SqlSession, app_name: str, user_id: str,
                                 state_delta: dict[str, Any]) -> dict[str, Any]:
        app_user_key = SqlKey(key=(app_name, user_id), storage_cls=StorageUserState)
        storage_user_state: Optional[StorageUserState] = await self._sql_storage.get(sql_session, app_user_key)

        user_state = storage_user_state.state if storage_user_state else {}
        if state_delta:
            user_state.update(state_delta)

        if not storage_user_state:
            storage_user_state = StorageUserState(app_name=app_name, user_id=user_id, state=user_state)
            await self._sql_storage.add(sql_session, storage_user_state)
        else:
            storage_user_state.state = user_state  # type: ignore
        storage_user_state.update_time = func.now()

        return user_state

    async def _get_app_state(self, sql_session: SqlSession, app_name: str) -> dict[str, Any]:
        app_key = SqlKey(key=(app_name, ), storage_cls=StorageAppState)
        storage_app_state: StorageAppState = await self._sql_storage.get(sql_session, app_key)

        app_state = {}
        if storage_app_state:
            if not self._session_config.is_expired_by_timestamp(storage_app_state.update_time.timestamp()):
                app_state = storage_app_state.state
                storage_app_state.update_time = func.now()
                await self._sql_storage.commit(sql_session)

        return app_state

    async def _get_user_state(self, sql_session: SqlSession, app_name: str, user_id: str) -> dict[str, Any]:
        app_user_key = SqlKey(key=(app_name, user_id), storage_cls=StorageUserState)
        storage_user_state: StorageUserState = await self._sql_storage.get(sql_session, app_user_key)

        user_state = {}
        if storage_user_state:
            if not self._session_config.is_expired_by_timestamp(storage_user_state.update_time.timestamp()):
                user_state = storage_user_state.state
                storage_user_state.update_time = func.now()
                await self._sql_storage.commit(sql_session)

        return user_state

    async def _get_session(self, sql_session: SqlSession, app_name: str, user_id: str,
                           session_id: str) -> Optional[StorageSession]:
        session_key = SqlKey(key=(app_name, user_id, session_id), storage_cls=StorageSession)
        storage_session: Optional[StorageSession] = await self._sql_storage.get(sql_session, session_key)
        if storage_session is None:
            return None

        if self._session_config.is_expired_by_timestamp(storage_session.update_time.timestamp()):
            logger.debug("Session %s is expired", session_id)
            return None

        storage_session.update_time = func.now()
        await self._sql_storage.commit(sql_session)

        return storage_session

    async def _cleanup_expired_async(self) -> None:
        """Async version of cleanup that deletes expired data from database.

        Uses SQL-level batch deletion for optimal performance.
        Deletes all expired data in three batch SQL DELETE statements.
        """
        async with self._sql_storage.create_db_session() as sql_session:
            # Calculate expiration threshold once (using database local time)
            expire_before = datetime.now() - timedelta(seconds=self._session_config.ttl.ttl_seconds)
            total_deleted = 0

            # Batch delete expired sessions
            session_filters = [StorageSession.update_time < expire_before]
            session_conditions = SqlCondition(filters=session_filters)
            session_key = SqlKey(key=tuple(), storage_cls=StorageSession)

            expired_sessions = await self._sql_storage.query(sql_session, session_key, session_conditions)
            session_count = len(expired_sessions) if expired_sessions else 0
            if session_count > 0:
                await self._sql_storage.delete(sql_session, session_key, session_conditions)
                total_deleted += session_count
                logger.debug("Batch deleted %s expired sessions", session_count)

            # Batch delete expired app states
            app_state_filters = [StorageAppState.update_time < expire_before]
            app_state_conditions = SqlCondition(filters=app_state_filters)
            app_state_key = SqlKey(key=tuple(), storage_cls=StorageAppState)

            expired_app_states = await self._sql_storage.query(sql_session, app_state_key, app_state_conditions)
            app_state_count = len(expired_app_states) if expired_app_states else 0
            if app_state_count > 0:
                await self._sql_storage.delete(sql_session, app_state_key, app_state_conditions)
                total_deleted += app_state_count
                logger.debug("Batch deleted %s expired app states", app_state_count)

            # Batch delete expired user states
            user_state_filters = [StorageUserState.update_time < expire_before]
            user_state_conditions = SqlCondition(filters=user_state_filters)
            user_state_key = SqlKey(key=tuple(), storage_cls=StorageUserState)

            expired_user_states = await self._sql_storage.query(sql_session, user_state_key, user_state_conditions)
            user_state_count = len(expired_user_states) if expired_user_states else 0
            if user_state_count > 0:
                await self._sql_storage.delete(sql_session, user_state_key, user_state_conditions)
                total_deleted += user_state_count
                logger.debug("Batch deleted %s expired user states", user_state_count)

            if total_deleted > 0:
                await self._sql_storage.commit(sql_session)
                logger.info("Cleanup completed: deleted %s items (%s sessions, %s app states, %s user states)",
                            total_deleted, session_count, app_state_count, user_state_count)

    async def _cleanup_loop(self) -> None:
        """Background task for periodic cleanup of expired sessions and states."""
        logger.debug("Cleanup task started with interval: %ss", self._session_config.ttl.cleanup_interval_seconds)

        try:
            while not self.__cleanup_stop_event.is_set():
                try:
                    await asyncio.wait_for(self.__cleanup_stop_event.wait(),
                                           timeout=self._session_config.ttl.cleanup_interval_seconds)
                    break
                except asyncio.TimeoutError:
                    try:
                        await self._cleanup_expired_async()
                        logger.debug("Cleanup cycle completed")
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.error("Error during cleanup: %s", ex, exc_info=True)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Cleanup loop encountered error: %s", ex, exc_info=True)
        finally:
            logger.debug("Cleanup task stopped")

    def _start_cleanup_task(self) -> None:
        """Start the background cleanup task."""
        if not self._session_config.need_ttl_expire():
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
