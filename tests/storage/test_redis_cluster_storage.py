# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Redis Cluster storage adapter without a live cluster."""

from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.storage import RedisClusterStorage
from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisCondition


class _FakeClusterClient:
    """Minimal async cluster client used to exercise the storage adapter."""

    def __init__(self) -> None:
        self.scan_calls: list[tuple[str, int]] = []
        self.closed = False

    def scan_iter(self, match: str, count: int):
        self.scan_calls.append((match, count))

        async def _iterate():
            # Duplicate keys are possible while a real cluster is resharding.
            yield b"memory:app/user:one"
            yield b"memory:app/user:two"
            yield b"memory:app/user:one"

        return _iterate()

    async def type(self, key: str):
        return "list"

    async def lrange(self, key: str, start: int, end: int):
        return [f'{key}-event']

    async def hgetall(self, key: str):
        return {b"field": b"value"}

    async def close(self):
        self.closed = True


class TestRedisClusterStorage:

    async def test_query_scans_all_cluster_keys_and_deduplicates(self):
        storage = RedisClusterStorage(redis_url="redis://seed:6379/0", is_async=True)
        client = _FakeClusterClient()
        storage._redis_client = client

        async with storage.create_db_session() as conn:
            results = await storage.query(conn, "memory:app/user:*", RedisCondition(limit=-1))

        assert [key for key, _ in results] == ["memory:app/user:one", "memory:app/user:two"]
        assert client.scan_calls == [("memory:app/user:*", storage._SCAN_COUNT)]

    async def test_keys_command_uses_scan_not_node_local_keys(self):
        storage = RedisClusterStorage(redis_url="redis://seed:6379/0", is_async=True)
        client = _FakeClusterClient()

        keys = await storage.execute_command(client, RedisCommand(method="keys", args=("session:app:*", )))

        assert keys == ["memory:app/user:one", "memory:app/user:two"]
        assert client.scan_calls == [("session:app:*", storage._SCAN_COUNT)]

    async def test_hgetall_normalizes_raw_redis_bytes(self):
        storage = RedisClusterStorage(redis_url="redis://seed:6379/0", is_async=True, decode_responses=False)
        client = _FakeClusterClient()

        result = await storage.execute_command(client, RedisCommand(method="hgetall", args=("state", )))

        assert result == {"field": "value"}

    async def test_async_client_is_created_from_seed_url(self):
        storage = RedisClusterStorage(redis_url="redis://seed:6379/0", is_async=True, max_connections=20)
        client = MagicMock()
        with patch("trpc_agent_sdk.storage._redis_cluster.AsyncRedisCluster") as client_cls:
            client_cls.from_url.return_value = client
            await storage.create_redis_engine()

        client_cls.from_url.assert_called_once_with("redis://seed:6379/0", decode_responses=True, max_connections=20)
        assert storage._redis_client is client

    async def test_close_releases_cluster_client(self):
        storage = RedisClusterStorage(redis_url="redis://seed:6379/0", is_async=True)
        client = _FakeClusterClient()
        storage._redis_client = client

        await storage.close()

        assert client.closed is True
        assert storage._redis_client is None
