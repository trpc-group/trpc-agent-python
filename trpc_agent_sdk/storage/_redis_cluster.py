# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redis Cluster storage implementation.

Unlike :class:`RedisStorage`, this adapter uses redis-py's native cluster
clients.  Key-based commands are routed to their owning hash slot by the
client.  Pattern queries use ``SCAN`` through ``RedisCluster.scan_iter`` so
they visit every primary node instead of issuing a node-local ``KEYS`` command.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any
from typing import Optional
from typing import Union

from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster
from redis.cluster import RedisCluster as SyncRedisCluster

from trpc_agent_sdk.log import logger

from ._redis import RedisCommand
from ._redis import RedisCondition
from ._redis import RedisExpire
from ._redis import RedisStorage


RedisClusterClient = Union[AsyncRedisCluster, SyncRedisCluster]


class RedisClusterAsyncContextManager:
    """Yield a shared Redis Cluster client without closing it per operation."""

    def __init__(self, redis_storage: "RedisClusterStorage") -> None:
        self._redis_storage = redis_storage
        self._client: Optional[RedisClusterClient] = None

    async def __aenter__(self) -> RedisClusterClient:
        self._client = await self._redis_storage.create_redis_session()
        return self._client

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # A cluster client owns pools for all discovered nodes.  It must remain
        # open for the storage lifetime, rather than being closed per request.
        self._client = None


class RedisClusterStorage(RedisStorage):
    """Storage adapter backed by redis-py's native Redis Cluster client.

    ``redis_url`` identifies one seed node.  redis-py discovers the remaining
    topology automatically; callers may alternatively pass ``startup_nodes``
    in ``kwargs``.  Redis Cluster only supports logical database 0.
    """

    _SCAN_COUNT = 1000

    def __init__(self, redis_url: str, is_async: bool = False, **kwargs: Any) -> None:
        # State hashes are consumed as normal Python string dictionaries by the
        # session services.  Keep that invariant unless callers explicitly ask
        # redis-py for raw responses.
        kwargs.setdefault("decode_responses", True)
        super().__init__(redis_url=redis_url, is_async=is_async, **kwargs)
        self._redis_client: Optional[RedisClusterClient] = None

    async def create_redis_engine(self) -> None:
        """Create the shared cluster-aware client lazily."""
        if self._redis_client:
            return
        try:
            client_cls = AsyncRedisCluster if self._is_async else SyncRedisCluster
            self._redis_client = client_cls.from_url(self._redis_url, **self._kwargs)
            logger.debug("Redis Cluster client created successfully")
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Failed to create Redis Cluster client for URL '{self._redis_url}'") from ex

    async def create_redis_session(self) -> RedisClusterClient:
        """Return the shared client after lazily constructing it."""
        await self.create_redis_engine()
        if not self._redis_client:
            raise ValueError("Redis Cluster client not initialized")
        return self._redis_client

    def create_db_session(self) -> RedisClusterAsyncContextManager:
        """Create an async context that borrows the shared cluster client."""
        return RedisClusterAsyncContextManager(self)

    async def delete(self, conn: RedisClusterClient, key: str, conditions: Optional[RedisCondition] = None) -> None:
        """Delete one key, letting redis-py route it to the correct slot."""
        ret = conn.delete(key)
        if inspect.isawaitable(ret):
            await ret

    async def query(self, conn: RedisClusterClient, key: str,
                    conditions: RedisCondition) -> list[tuple[str, Any]]:
        """Query keys across all cluster primaries using cursor-based scanning."""
        keys = await self._scan_keys(conn, key)
        if conditions.limit > 0:
            keys = keys[:conditions.limit]

        results: list[tuple[str, Any]] = []
        for redis_key in keys:
            try:
                key_type = await self.execute_command(conn, RedisCommand(method="type", args=(redis_key, )))
                key_type = self._decode_text(key_type)

                if key_type == "string":
                    value = await self.execute_command(conn, RedisCommand(method="get", args=(redis_key, )))
                    if value is not None:
                        results.append((redis_key, self._deserialize_value(value)))
                elif key_type == "hash":
                    hash_data = await self.execute_command(conn, RedisCommand(method="hgetall", args=(redis_key, )))
                    if hash_data:
                        results.append((redis_key, hash_data))
                elif key_type == "list":
                    list_data = await self.execute_command(conn, RedisCommand(method="lrange", args=(redis_key, 0, -1)))
                    if list_data:
                        results.append((redis_key, list_data))
                elif key_type == "set":
                    set_data = await self.execute_command(conn, RedisCommand(method="smembers", args=(redis_key, )))
                    if set_data:
                        results.append((redis_key, set_data))
                elif key_type == "zset":
                    zset_data = await self.execute_command(
                        conn, RedisCommand(method="zrange", args=(redis_key, 0, -1), kwargs={"withscores": True}))
                    if zset_data:
                        results.append((redis_key, zset_data))
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to query Redis Cluster key '%s': %s", redis_key, ex)
        return results

    async def execute_command(self, conn: RedisClusterClient, command: RedisCommand) -> Any:
        """Execute a command, replacing node-local ``KEYS`` with cluster ``SCAN``."""
        lower_method = command.method.lower()
        if lower_method == "keys":
            if not command.args:
                raise ValueError("Redis KEYS command requires a match pattern")
            return await self._scan_keys(conn, self._decode_text(command.args[0]))

        result = await super().execute_command(conn, command)
        if lower_method == "hgetall" and isinstance(result, dict):
            return {self._decode_text(k): self._decode_text(v) for k, v in result.items()}
        return result

    async def close(self) -> None:
        """Close all pools held by the shared Redis Cluster client."""
        if not self._redis_client:
            return
        try:
            close_method = getattr(self._redis_client, "aclose", None) if self._is_async else None
            if close_method is None:
                close_method = self._redis_client.close
            ret = close_method()
            if inspect.isawaitable(ret):
                await ret
        except Exception as ex:  # pylint: disable=broad-except
            logger.info("Failed to close Redis Cluster client: %s", ex)
        finally:
            self._redis_client = None

    async def _scan_keys(self, conn: RedisClusterClient, match: str) -> list[str]:
        """Return de-duplicated keys from every primary node in the cluster.

        ``RedisCluster.scan_iter`` starts its first scan on all nodes.  This is
        essential because ``KEYS`` without an explicit target node is scoped to
        one default node in redis-py Cluster.
        """
        iterator = conn.scan_iter(match=match, count=self._SCAN_COUNT)
        if inspect.isawaitable(iterator):
            iterator = await iterator

        keys: list[str] = []
        seen: set[str] = set()

        def _append(raw_key: Any) -> None:
            normalized_key = self._decode_text(raw_key)
            if normalized_key not in seen:
                seen.add(normalized_key)
                keys.append(normalized_key)

        if hasattr(iterator, "__aiter__"):
            async for raw_key in iterator:
                _append(raw_key)
        else:
            for raw_key in iterator:
                _append(raw_key)
        return keys

    @staticmethod
    def _decode_text(value: Any) -> str:
        """Decode Redis bytes while preserving normal string responses."""
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _deserialize_value(self, value: Any) -> Any:
        """Deserialize String values returned as bytes or decoded text."""
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                value_str = value.decode("utf-8")
            except UnicodeDecodeError:
                return value
        elif isinstance(value, str):
            value_str = value
        else:
            return value
        try:
            return json.loads(value_str)
        except json.JSONDecodeError:
            return value_str
