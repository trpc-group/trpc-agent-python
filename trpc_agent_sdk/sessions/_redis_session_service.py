# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""Redis session service implementation."""

from __future__ import annotations

import time
import uuid
from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import ListSessionsResponse
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisExpire
from trpc_agent_sdk.storage import RedisSession
from trpc_agent_sdk.storage import RedisStorage
from trpc_agent_sdk.utils import user_key

from ._base_session_service import BaseSessionService
from ._session import Session
from ._summarizer_manager import SummarizerSessionManager
from ._types import SessionServiceConfig
from ._utils import StateStorageEntry
from ._utils import app_state_key
from ._utils import extract_state_delta
from ._utils import merge_state
from ._utils import session_key
from ._utils import user_state_key


def _decode_redis_hash(raw_state: Optional[dict[Any, Any]]) -> dict[str, Any]:
    """Normalize Redis hash responses to plain Python strings when possible."""

    if not raw_state:
        return {}

    normalized: dict[str, Any] = {}
    for raw_key, raw_value in raw_state.items():
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
        value = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else raw_value
        normalized[str(key)] = value
    return normalized


def _session_key_prefix(app_name: str, user_id: Optional[str] = None) -> str:
    """Generate a Redis key prefix for listing sessions.

    When user_id is None, the prefix matches sessions across all users for the
    given app; otherwise it is scoped to the specific user.

    Args:
        app_name: Application name
        user_id: Optional user identifier

    Returns:
        Formatted session key prefix with a trailing wildcard.
    """
    if user_id is None:
        return f"session:{app_name}:*"
    return f"session:{app_name}:{user_id}:*"


class RedisSessionService(BaseSessionService):
    """A Redis implementation of the session service.

    This service stores sessions in Redis with TTL support for automatic expiration.
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
    """

    def __init__(self,
                 db_url: str,
                 summarizer_manager: Optional[SummarizerSessionManager] = None,
                 session_config: Optional[SessionServiceConfig] = None,
                 is_async: bool = False,
                 **kwargs: Any):
        is_default_config = session_config is None
        super().__init__(summarizer_manager=summarizer_manager, session_config=session_config)
        if is_default_config:
            # Default to store historical events for persistent backends.
            self._session_config.store_historical_events = True
        # Redis needs default TTL configuration
        self._redis_storage = self._create_storage(db_url=db_url, is_async=is_async, **kwargs)

    def _create_storage(self, db_url: str, is_async: bool, **kwargs: Any) -> RedisStorage:
        """Create the backing storage.

        Subclasses override this factory to retain the session semantics while
        selecting a different Redis deployment client, such as Redis Cluster.
        """
        return RedisStorage(is_async=is_async, redis_url=db_url, **kwargs)

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
        state_deltas = extract_state_delta(state)

        async with self._redis_storage.create_db_session() as redis_session:
            # Create session with session-scoped state only
            # Get existing app and user states
            app_state = await self._update_app_state(redis_session, app_name, state_deltas.app_state_delta)
            user_state = await self._update_user_state(redis_session, app_name, user_id, state_deltas.user_state_delta)
            session_id = session_id.strip() if session_id and session_id.strip() else str(uuid.uuid4())
            session = Session(
                id=session_id,
                app_name=app_name,
                user_id=user_id,
                state=state_deltas.session_state,
                last_update_time=time.time(),
                save_key=user_key(app_name, user_id),
            )

            # Save session to Redis with TTL
            await self._set_session(redis_session, session)

            # redis session has been stored, so we can return the session with merged state
            return self._merge_state(app_state, user_state, session)

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: Optional[AgentContext] = None,
    ) -> Optional[Session]:
        async with self._redis_storage.create_db_session() as redis_session:
            redis_session_key = session_key(app_name, user_id, session_id)
            storage_session = await self._get_session(redis_session, redis_session_key)

            if not storage_session:
                return None

            # Filter events for the returned view without mutating storage data.
            session = self.filter_events(storage_session)

            # Get and merge state
            app_state = await self._get_app_state(redis_session, app_name)
            user_state = await self._get_user_state(redis_session, app_name, user_id)

            return self._merge_state(app_state, user_state, session)

    @override
    async def list_sessions(self, *, app_name: str, user_id: Optional[str] = None) -> ListSessionsResponse:
        async with self._redis_storage.create_db_session() as redis_session:
            pattern = _session_key_prefix(app_name, user_id)
            command = RedisCommand(method='keys', args=(pattern, ))
            keys = await self._redis_storage.execute_command(redis_session, command)

            if not keys:
                return ListSessionsResponse()

            # Get app state once for all sessions
            app_state = await self._get_app_state(redis_session, app_name)

            sessions_without_events = []
            for key in keys:
                storage_session = await self._get_session(redis_session, key)
                if storage_session:
                    # Clear events for list view
                    storage_session.events = []
                    storage_session.historical_events = []
                    # Merge state
                    user_state = await self._get_user_state(redis_session, app_name, storage_session.user_id)
                    storage_session = self._merge_state(app_state, user_state, storage_session)
                    sessions_without_events.append(storage_session)

            return ListSessionsResponse(sessions=sessions_without_events)

    @override
    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        async with self._redis_storage.create_db_session() as redis_session:
            key = session_key(app_name, user_id, session_id)
            await self._redis_storage.delete(redis_session, key)

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        # Skip partial events
        if event.partial:
            return event

        # Update the in-memory session
        await super().append_event(session=session, event=event)

        # Update storage
        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id

        def _warning(message: str) -> None:
            logger.warning("Failed to append event to session %s: %s", session_id, message)

        async with self._redis_storage.create_db_session() as redis_session:
            redis_session_key = session_key(app_name, user_id, session_id)

            # Get storage session
            storage_session = await self._get_session(redis_session, redis_session_key)
            if not storage_session:
                _warning("session not found in Redis")
                return event
            # Extract and apply state changes to appropriate storage buckets
            if event.actions and event.actions.state_delta:
                state_delta = extract_state_delta(event.actions.state_delta)

                # Update app state and refresh TTL
                if state_delta.app_state_delta:
                    await self._update_app_state(redis_session, app_name, state_delta.app_state_delta)

                # Update user state and refresh TTL
                if state_delta.user_state_delta:
                    await self._update_user_state(redis_session, app_name, user_id, state_delta.user_state_delta)

                # Update session state
                if state_delta.session_state:
                    storage_session.state.update(state_delta.session_state)

            storage_session.events = session.events
            storage_session.historical_events = session.historical_events
            storage_session.conversation_count = session.conversation_count
            await self._set_session(redis_session, storage_session)

        return event

    @override
    async def update_session(self, session: Session) -> None:
        """Update a session in storage.

        Args:
            session: The session to update
        """
        async with self._redis_storage.create_db_session() as redis_session:
            key = session_key(session.app_name, session.user_id, session.id)
            storage_session = await self._get_session(redis_session, key)
            if not storage_session:
                logger.warning("Session %s not found in Redis for app %s, user %s. It will be created.", session.id,
                               session.app_name, session.user_id)
                return
            await self._set_session(redis_session, session)

    @override
    async def close(self) -> None:
        """Close the service and release resources."""
        if self._redis_storage:
            await self._redis_storage.close()
        await super().close()

    async def _update_app_state(self, redis_session: RedisSession, app_name: str,
                                state_delta: dict[str, Any]) -> dict[str, Any]:
        """Update app state in Redis and refresh TTL.

        Note: TTL is refreshed on update to match InMemorySessionService behavior.

        Args:
            redis_session: Redis session
            app_name: Application name
            state_delta: State changes to apply
        """

        key = app_state_key(app_name)
        command = RedisCommand(method='hgetall', args=(key, ))
        app_state = _decode_redis_hash(await self._redis_storage.execute_command(redis_session, command))
        if app_state:
            app_state.update(state_delta)
        else:
            app_state = state_delta

        if not app_state:
            return {}

        if not state_delta:
            await self._refresh_ttl(redis_session, key)
            return app_state

        command = RedisCommand(method='hset',
                               args=(key, ),
                               kwargs={"mapping": app_state},
                               expire=RedisExpire(key=key, ttl=self._session_config.ttl))
        await self._redis_storage.execute_command(redis_session, command)

        return app_state

    async def _update_user_state(self, redis_session: RedisSession, app_name: str, user_id: str,
                                 state_delta: dict[str, Any]) -> dict[str, Any]:
        """Update user state in Redis and refresh TTL.

        Note: TTL is refreshed on update to match InMemorySessionService behavior.

        Args:
            redis_session: Redis session
            app_name: Application name
            user_id: User ID
            state_delta: State changes to apply
        """

        key = user_state_key(app_name, user_id)
        command = RedisCommand(method='hgetall', args=(key, ))
        user_state = _decode_redis_hash(await self._redis_storage.execute_command(redis_session, command))
        if user_state:
            user_state.update(state_delta)
        else:
            user_state = state_delta

        if not user_state:
            return {}

        if not state_delta:
            await self._refresh_ttl(redis_session, key)
            return user_state

        command = RedisCommand(method='hset',
                               args=(key, ),
                               kwargs={"mapping": user_state},
                               expire=RedisExpire(key=key, ttl=self._session_config.ttl))
        await self._redis_storage.execute_command(redis_session, command)

        return user_state

    async def _set_session(self, redis_session: RedisSession, session: Session) -> None:
        """Set the session in Redis with TTL support.

        Args:
            redis_session: Redis session
            session: Session to set
        """
        key = session_key(session.app_name, session.user_id, session.id)
        if self._session_config.store_historical_events:
            session_json = session.model_dump_json()
        else:
            session_json = session.model_copy(update={"historical_events": []}).model_dump_json()

        # Use SET with TTL if TTL is configured, otherwise use SET
        command = RedisCommand(method='set',
                               args=(key, session_json),
                               expire=RedisExpire(key=key, ttl=self._session_config.ttl))
        await self._redis_storage.execute_command(redis_session, command)

    async def _get_app_state(self, redis_session: RedisSession, app_name: str) -> dict[str, Any]:
        """Get app state from Redis and refresh TTL.

        Note: TTL is refreshed on access to match InMemorySessionService behavior.

        Args:
            redis_session: Redis session
            app_name: Application name

        Returns:
            App state dictionary
        """
        key = app_state_key(app_name)
        command = RedisCommand(method='hgetall', args=(key, ))
        app_state = _decode_redis_hash(await self._redis_storage.execute_command(redis_session, command))
        if app_state:
            await self._refresh_ttl(redis_session, key)

        return app_state or {}

    async def _get_user_state(self, redis_session: RedisSession, app_name: str, user_id: str) -> dict[str, Any]:
        """Get user state from Redis and refresh TTL.

        Note: TTL is refreshed on access to match InMemorySessionService behavior.

        Args:
            redis_session: Redis session
            app_name: Application name
            user_id: User ID

        Returns:
            User state dictionary
        """
        key = user_state_key(app_name, user_id)
        command = RedisCommand(method='hgetall', args=(key, ))
        user_state = _decode_redis_hash(await self._redis_storage.execute_command(redis_session, command))
        if user_state:
            await self._refresh_ttl(redis_session, key)
        return user_state or {}

    async def _get_session(self, redis_session: RedisSession, session_key: str) -> Optional[Session]:
        """Get the session from Redis.

        Args:
            redis_session: Redis session
            session_key: Full Redis key for the session

        Returns:
            Session object if found, None otherwise
        """
        command = RedisCommand(method='get', args=(session_key, ))
        storage_session_data = await self._redis_storage.execute_command(redis_session, command)
        if storage_session_data:
            await self._refresh_ttl(redis_session, session_key)
            session = Session.model_validate_json(storage_session_data)
            if not self._session_config.store_historical_events:
                session.historical_events = []
            return session
        return None

    def _merge_state(self, app_state: dict[str, Any], user_state: dict[str, Any], session: Session) -> Session:
        """Merge app, user, and session state into the session object.

        Note: This method receives already-refreshed state from _get_app_state
        and _get_user_state, which handle TTL refresh on access.

        Args:
            app_state: Application-level state
            user_state: User-level state
            session: Session to merge state into

        Returns:
            Session with merged state
        """
        # Merge states using utility function
        state_entry = StateStorageEntry(app_state_delta=app_state,
                                        user_state_delta=user_state,
                                        session_state=session.state)
        merge_state(state_entry, need_copy=False)
        return session

    async def _refresh_ttl(self, redis_session: RedisSession, key: str) -> None:
        """Refresh the TTL for a key in Redis.

        Args:
            redis_session: Redis session
            key: The key to refresh TTL for
        """
        if not self._session_config.need_ttl_expire():
            return
        await self._redis_storage.expire(redis_session, RedisExpire(key=key, ttl=self._session_config.ttl))
