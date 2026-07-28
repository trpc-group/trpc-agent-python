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


def _decode_state_hash(raw_state: Any) -> dict[str, Any]:
    """Decode Redis Hash keys and values at the Session state boundary.

    在 Session state 边界解码 Redis Hash 的键和值；仅处理 Redis 返回的
    ``bytes``，不改变 mock、Cluster 或显式解码客户端返回的原生 Python 值。
    """
    if not isinstance(raw_state, dict):
        return {}

    decoded: dict[str, Any] = {}
    for raw_key, raw_value in raw_state.items():
        # Redis state keys are textual. Decode raw responses so a later str
        # state_delta overwrites the same field instead of creating bytes/str
        # duplicates in the intermediate dictionary.
        # Redis state 键为文本；解码原始响应，避免后续 str 类型 delta 与 bytes
        # 类型旧键并存，导致同一字段被当成两个 HSET 项。
        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
        value = raw_value
        if isinstance(raw_value, bytes):
            try:
                value = raw_value.decode("utf-8")
            except UnicodeDecodeError:
                # Preserve non-text values rather than corrupting their bytes.
                # 非文本值保持原始 bytes，避免错误解码造成数据损坏。
                value = raw_value
        decoded[str(key)] = value
    return decoded


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
        """Append an event idempotently and persist the session to Redis.

        以 Event ID 保证追加幂等，并将调用方 Session 快照保存到 Redis；若前次
        仅完成本地修改，本次重试会检测 Redis 缺失并继续补写。
        """
        # Partial streaming events are not durable session history.
        # 流式 partial 事件不属于可持久化的会话历史。
        if event.partial:
            return event

        # Update the caller copy first and retain whether this invocation
        # actually appended the ID locally.
        # 先更新调用方副本，并记录本次是否真正向本地追加了该 Event ID。
        event, _, appended = self._append_event_to_session(session, event)

        # Resolve the Redis key scope from the caller-owned session identity.
        # 根据调用方 Session 标识确定 Redis key 的作用域。
        app_name = session.app_name
        user_id = session.user_id
        session_id = session.id

        def _warning(message: str) -> None:
            logger.warning("Failed to append event to session %s: %s", session_id, message)

        async with self._redis_storage.create_db_session() as redis_session:
            redis_session_key = session_key(app_name, user_id, session_id)

            # Read the durable snapshot before deciding whether a local
            # duplicate can safely short-circuit.
            # 在判断本地重复能否直接返回前，先读取 Redis 中的持久化快照。
            storage_session = await self._get_session(redis_session, redis_session_key)
            if not storage_session:
                _warning("session not found in Redis")
                return event
            # Local and Redis copies both contain the ID: the retry is complete.
            # If only the caller has it, continue to repair the missing Redis write.
            # 本地与 Redis 都有该 ID 才直接返回；仅本地存在时继续补偿 Redis 写入。
            if not appended and any(stored.id == event.id
                                    for stored in [*storage_session.events, *storage_session.historical_events]):
                return event
            # Split the delta into the same app/user/session scopes used by reads.
            # 按读取语义拆分 delta，并分别写入 app/user/session 作用域。
            if event.actions and event.actions.state_delta:
                state_delta = extract_state_delta(event.actions.state_delta)

                # Update app state and refresh TTL / 更新应用 state 并刷新 TTL。
                if state_delta.app_state_delta:
                    await self._update_app_state(redis_session, app_name, state_delta.app_state_delta)

                # Update user state and refresh TTL / 更新用户 state 并刷新 TTL。
                if state_delta.user_state_delta:
                    await self._update_user_state(redis_session, app_name, user_id, state_delta.user_state_delta)

                # Update session state / 更新会话级 state。
                if state_delta.session_state:
                    storage_session.state.update(state_delta.session_state)

            # Persist the complete caller-side windows so normal writes and
            # failure-recovery retries converge to the same Redis snapshot.
            # 写入调用方的完整事件窗口，使首次写入与失败重试最终得到同一 Redis 快照。
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
        app_state = _decode_state_hash(await self._redis_storage.execute_command(redis_session, command))
        if app_state:
            app_state.update(state_delta)
        else:
            app_state = dict(state_delta)

        if not app_state:
            return {}

        if not state_delta:
            await self._refresh_ttl(redis_session, key)
            return app_state

        # redis-py HSET accepts multiple fields through ``mapping``. Flattened
        # positional pairs bind the fourth argument as ``mapping`` and fail on
        # redis-py 8 when more than one field is present.
        # redis-py 的 HSET 通过 ``mapping`` 接收多字段；展开的位置参数在字段
        # 超过一个时会把第 4 个参数绑定为 mapping，并在 redis-py 8 中报错。
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
        user_state = _decode_state_hash(await self._redis_storage.execute_command(redis_session, command))
        if user_state:
            user_state.update(state_delta)
        else:
            user_state = dict(state_delta)

        if not user_state:
            return {}

        if not state_delta:
            await self._refresh_ttl(redis_session, key)
            return user_state

        # Keep user-scoped state on the same redis-py mapping path as app state.
        # 用户级 state 与应用级 state 使用相同的 redis-py mapping 写入语义。
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
        app_state = _decode_state_hash(await self._redis_storage.execute_command(redis_session, command))
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
        user_state = _decode_state_hash(await self._redis_storage.execute_command(redis_session, command))
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
