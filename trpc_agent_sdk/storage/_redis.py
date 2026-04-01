# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Redis storage implementation."""
import asyncio
import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional
from typing import Tuple
from typing import TypeAlias
from typing import Union
from typing_extensions import override

from redis.asyncio import ConnectionPool as AsyncConnectionPool
from redis.asyncio import Redis as AsyncRedis
from redis.client import Redis as SyncRedis
from redis.connection import ConnectionPool as SyncConnectionPool

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Ttl

from ._db import BaseStorage

RedisSession: TypeAlias = Union[AsyncRedis, SyncRedis]
RedisConnectionPool: TypeAlias = Union[AsyncConnectionPool, SyncConnectionPool]

EXPIRE_METHOD: list[str] = [
    'set',  # String: set single key
    'hset',  # Hash: set hash field
    'hmset',  # Hash: set multiple hash fields (deprecated but still used)
    'lpush',  # List: push to left
    'rpush',  # List: push to right
    'sadd',  # Set: add member
    'zadd',  # Sorted Set: add member with score
]


@dataclass
class RedisExpire:
    """Redis expire command."""
    key: str = ""
    method: str = "expire"
    ttl: Ttl = field(default_factory=Ttl)
    args: Tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)


@dataclass
class RedisCommand:
    """Redis command."""
    method: str
    expire: RedisExpire = field(default_factory=RedisExpire)
    args: Tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)


@dataclass
class RedisCondition:
    """Redis condition."""
    limit: int = -1
    """Limit the number of results to return."""


class RedisAsyncContextManager:
    """Async context manager for Redis sessions."""

    def __init__(self, redis_storage: 'RedisStorage') -> None:
        self.redis_storage = redis_storage
        self._session: Optional[RedisSession] = None

    async def __aenter__(self) -> RedisSession:
        """Acquire Redis connection."""
        self._session = await self.redis_storage.create_redis_session()
        if isinstance(self._session, AsyncRedis):
            await self._session.__aenter__()
        else:
            self._session.__enter__()
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Release Redis connection."""
        if not self._session:
            return
        if isinstance(self._session, AsyncRedis):
            await self._session.__aexit__(exc_type, exc_val, exc_tb)
        else:
            self._session.__exit__(exc_type, exc_val, exc_tb)


class RedisStorage(BaseStorage):
    """Redis storage implementation."""

    def __init__(self, redis_url: str, is_async: bool = False, **kwargs: Any) -> None:
        super().__init__()
        self._redis_url = redis_url
        self._is_async = is_async
        self._kwargs = kwargs
        self._redis_pool: Optional[RedisConnectionPool] = None

    async def create_redis_engine(self) -> None:
        """Create Redis connection pool."""
        if self._redis_pool:
            return

        try:
            if self._is_async:
                self._redis_pool = AsyncConnectionPool.from_url(self._redis_url, **self._kwargs)
            else:
                self._redis_pool = SyncConnectionPool.from_url(self._redis_url, **self._kwargs)
            logger.debug("Redis connection pool created successfully")
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Failed to create Redis connection pool for URL '{self._redis_url}'") from ex

    async def create_redis_session(self) -> RedisSession:
        """Create Redis session."""
        await self.create_redis_engine()
        if not self._redis_pool:
            raise ValueError("Redis connection pool not initialized")
        if isinstance(self._redis_pool, AsyncConnectionPool):
            return AsyncRedis(connection_pool=self._redis_pool)
        return SyncRedis(connection_pool=self._redis_pool)

    def create_db_session(self) -> RedisAsyncContextManager:
        """Create Redis session context."""
        return RedisAsyncContextManager(self)

    def _serialize_value(self, value: Any) -> str:
        """Serialize value to JSON string."""
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        return json.dumps(value, default=str)

    def _deserialize_value(self, value: Optional[bytes]) -> Any:
        """Deserialize value from Redis bytes."""
        if value is None:
            return None

        try:
            value_str = value.decode('utf-8')
            # Try to parse as JSON first
            try:
                return json.loads(value_str)
            except json.JSONDecodeError:
                # If not JSON, return as string
                return value_str
        except UnicodeDecodeError:
            return value

    @override
    async def add(self, conn: RedisSession, data: RedisCommand) -> None:
        """Add data to Redis."""
        if data.method.lower() not in EXPIRE_METHOD:
            raise ValueError(f"Invalid Redis set method: {data.method.lower()}")
        await self.execute_command(conn, data)

    @override
    async def delete(self, conn: RedisSession, key: str, conditions: Optional[RedisCondition] = None) -> None:
        """Delete data from Redis."""
        if isinstance(conn, AsyncRedis):
            await conn.delete(key)
            return
        conn.delete(key)

    @override
    async def get(self, conn: RedisSession, key: RedisCommand) -> Any:
        """Get value by key."""
        if 'get' not in key.method.lower():
            raise ValueError(f"Invalid Redis get method: {key.method.lower()}")
        return await self.execute_command(conn, key)

    @override
    async def query(self, conn: RedisSession, key: str, conditions: RedisCondition) -> list[tuple[str, Any]]:
        """Query data from Redis."""
        keys: list[str] = await self.execute_command(conn, RedisCommand(method='keys', args=(key, )))
        results: list[tuple[str, Any]] = []

        # Apply limit to keys if specified
        if conditions.limit > 0:
            keys = keys[:conditions.limit]

        for redis_key in keys:
            try:
                # Check the type of the key first
                key_type = await self.execute_command(conn, RedisCommand(method='type', args=(redis_key, )))
                if isinstance(key_type, bytes):
                    key_type = key_type.decode('utf-8')

                # Get value based on the key type
                if key_type == "string":
                    value = await self.execute_command(conn, RedisCommand(method='get', args=(redis_key, )))
                    if value:
                        results.append((redis_key, self._deserialize_value(value)))
                elif key_type == "hash":
                    hash_data = await self.execute_command(conn, RedisCommand(method='hgetall', args=(redis_key, )))
                    if hash_data:
                        results.append((redis_key, hash_data))
                elif key_type == "list":
                    list_data = await self.execute_command(conn, RedisCommand(method='lrange', args=(redis_key, 0, -1)))
                    if list_data:
                        results.append((redis_key, list_data))
                elif key_type == "set":
                    set_data = await self.execute_command(conn, RedisCommand(method='smembers', args=(redis_key, )))
                    if set_data:
                        results.append((redis_key, set_data))
                elif key_type == "zset":
                    zset_data = await self.execute_command(
                        conn, RedisCommand(method='zrange', args=(redis_key, 0, -1), kwargs={"withscores": True}))
                    if zset_data:
                        results.append((redis_key, zset_data))

            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to query key '%s': %s", redis_key, ex)
                continue
        return results

    @override
    async def commit(self, conn: RedisSession) -> None:
        """Commit changes (Redis operations are atomic by default)."""
        # Redis operations are atomic by default, so no explicit commit needed
        pass

    @override
    async def refresh(self, conn: RedisSession, data: Any) -> None:
        """Refresh data from Redis."""
        pass

    @override
    async def close(self) -> None:
        """Close Redis connection pool."""
        if not self._redis_pool:
            return
        try:
            if isinstance(self._redis_pool, AsyncConnectionPool):
                await self._redis_pool.disconnect()
            else:
                self._redis_pool.disconnect()
        except Exception as ex:  # pylint: disable=broad-except
            logger.info("Failed to close Redis connection pool: %s", ex)

    def __getattr__(self, item):
        """
        Override attribute access to provide dynamic command methods.

        Args:
            item (str): The attribute name (command name).

        Returns:
            Callable: An async function that calls do(ctx, item, args).
        """

        async def func(*args, **kwargs):
            if not self._redis_pool:
                return
            async with self.create_db_session() as conn:
                command = RedisCommand(method=item, args=args, kwargs=kwargs)
                return await self.execute_command(conn, command)

        return func

    async def execute_command(self, conn: RedisSession, command: RedisCommand) -> Any:
        """Execute Redis command."""
        lower_method = command.method.lower()
        upper_method = command.method.upper()
        method = getattr(conn, lower_method, None)
        if method:
            ret = method(*command.args, **command.kwargs)
        else:
            ret = conn.execute_command(upper_method, *command.args, **command.kwargs)
        if asyncio.iscoroutine(ret):
            ret = await ret
        if lower_method in EXPIRE_METHOD and command.args:
            if not command.expire.key:
                command.expire.key = command.args[0]
            await self.expire(conn, command.expire)
        return ret

    async def expire(self, conn: RedisSession, command: RedisExpire) -> Any:
        """Set expiration time for a Redis key.

        Args:
            conn: Redis connection
            command: RedisExpire object

        Returns:
            Result of the expire command

        Note:
            - If TTL is disabled, no expiration is set
        """
        # Check if enable is valid
        if not command.ttl.need_ttl_expire():
            return

        lower_method = command.method.lower()
        upper_method = command.method.upper()
        method = getattr(conn, lower_method, None)
        args = command.args
        if not args:
            args = (command.key, int(command.ttl.ttl_seconds))
        if method:
            ret = method(*args, **command.kwargs)
        else:
            ret = conn.execute_command(upper_method, *args, **command.kwargs)
        if asyncio.iscoroutine(ret):
            ret = await ret
        return ret
