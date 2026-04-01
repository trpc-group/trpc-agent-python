# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""A Redis-based memory service for prototyping and multi-node sharing."""

from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import MemoryServiceABC as BaseMemoryService
from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.events import Event as EventCls
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisCondition
from trpc_agent_sdk.storage import RedisExpire
from trpc_agent_sdk.storage import RedisStorage
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import SearchMemoryResponse

from ._utils import extract_words_lower
from ._utils import format_timestamp


class RedisMemoryService(BaseMemoryService):
    """A Redis-based memory service for prototyping and multi-node sharing.

    Uses keyword matching instead of semantic search.
    Stores events in Redis as JSON.
    """

    def __init__(
        self,
        db_url: str,
        enabled: bool = False,
        is_async: bool = False,
        memory_service_config: Optional[MemoryServiceConfig] = None,
        **kwargs: Any,
    ):
        super().__init__(memory_service_config=memory_service_config, enabled=enabled)
        # Redis needs default TTL configuration
        self._redis_storage = RedisStorage(is_async=is_async, redis_url=db_url, **kwargs)

    @override
    async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
        # Store all events for the session in a Redis list as JSON
        async with self._redis_storage.create_db_session() as redis_session:
            key = f"memory:{session.save_key}:{session.id}"
            events_json = [event.model_dump_json() for event in session.events if event.content and event.content.parts]
            if events_json:
                args = [key]
                args.extend(events_json)
                await self._redis_storage.delete(redis_session, key)  # Remove old events
                expire = RedisExpire(key=key, ttl=self._memory_service_config.ttl)
                command = RedisCommand(method='rpush', args=tuple(args), expire=expire)
                await self._redis_storage.execute_command(redis_session, command)

    @override
    async def search_memory(self,
                            key: str,
                            query: str,
                            limit: int = 10,
                            agent_context: Optional[AgentContext] = None) -> SearchMemoryResponse:
        response = SearchMemoryResponse()
        async with self._redis_storage.create_db_session() as redis_session:
            pattern = f"memory:{key}:*"
            events_json_list = await self._redis_storage.query(redis_session, pattern, RedisCondition(limit=-1))
            # Extract words from query (handles both English and Chinese)
            words_in_query = extract_words_lower(query)
            count = 0
            for redis_key, event_json in events_json_list:
                has_valid_event = False
                event = None

                if not isinstance(event_json, list):
                    event_json = [event_json]

                for data in event_json:
                    try:
                        event = EventCls.model_validate_json(data)
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.error("Error parsing event JSON: %s", ex)
                        continue
                    if not event or not event.content or not event.content.parts:
                        continue
                    words_in_event = extract_words_lower(' '.join(
                        [part.text for part in event.content.parts if part.text]))
                    if not words_in_event:
                        continue
                    if any(query_word in words_in_event for query_word in words_in_query):
                        response.memories.append(
                            MemoryEntry(
                                content=event.content,
                                author=event.author,
                                timestamp=format_timestamp(event.timestamp),
                            ))
                        count += 1
                        has_valid_event = True
                        if limit > 0 and count >= limit:
                            break
                # Refresh TTL on accessed keys that contain valid (non-expired) events
                if has_valid_event:
                    expire = RedisExpire(key=redis_key, ttl=self._memory_service_config.ttl)
                    await self._redis_storage.expire(redis_session, expire)
                if limit > 0 and count >= limit:
                    break
        return response

    @override
    async def close(self) -> None:
        await self._redis_storage.close()
        await super().close()
