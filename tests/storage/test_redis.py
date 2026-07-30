# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for Redis storage implementation."""
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.storage import EXPIRE_METHOD
from trpc_agent_sdk.storage import RedisAsyncContextManager
from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisCondition
from trpc_agent_sdk.storage import RedisExpire
from trpc_agent_sdk.storage import RedisStorage
from trpc_agent_sdk.types import Ttl


class TestRedisStorage:
    """Test suite for RedisStorage class."""

    @pytest.fixture
    def redis_url(self):
        """Redis URL fixture."""
        return "redis://localhost:6379/0"

    @pytest.fixture
    def sync_storage(self, redis_url):
        """Synchronous Redis storage fixture."""
        return RedisStorage(redis_url=redis_url, is_async=False)

    @pytest.fixture
    def async_storage(self, redis_url):
        """Asynchronous Redis storage fixture."""
        return RedisStorage(redis_url=redis_url, is_async=True)

    def test_init(self, redis_url):
        """Test RedisStorage initialization."""
        storage = RedisStorage(redis_url=redis_url, is_async=True, max_connections=10, decode_responses=True)

        assert storage._redis_url == redis_url
        assert storage._is_async is True
        assert storage._kwargs == {"max_connections": 10, "decode_responses": True}
        assert storage._redis_pool is None

    @pytest.mark.asyncio
    async def test_create_redis_engine_async(self, async_storage):
        """Test creating async Redis connection pool."""
        with patch('trpc_agent_sdk.storage._redis.AsyncConnectionPool') as mock_pool:
            mock_pool.from_url.return_value = MagicMock()

            await async_storage.create_redis_engine()

            mock_pool.from_url.assert_called_once_with(async_storage._redis_url)
            assert async_storage._redis_pool is not None

    @pytest.mark.asyncio
    async def test_create_redis_engine_sync(self, sync_storage):
        """Test creating sync Redis connection pool."""
        with patch('trpc_agent_sdk.storage._redis.SyncConnectionPool') as mock_pool:
            mock_pool.from_url.return_value = MagicMock()

            await sync_storage.create_redis_engine()

            mock_pool.from_url.assert_called_once_with(sync_storage._redis_url)
            assert sync_storage._redis_pool is not None

    @pytest.mark.asyncio
    async def test_create_redis_engine_already_exists(self, async_storage):
        """Test creating Redis engine when pool already exists."""
        async_storage._redis_pool = MagicMock()

        with patch('trpc_agent_sdk.storage._redis.AsyncConnectionPool') as mock_pool:
            await async_storage.create_redis_engine()
            # Should not create new pool
            mock_pool.from_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_redis_engine_error(self, async_storage):
        """Test error handling when creating Redis engine."""
        with patch('trpc_agent_sdk.storage._redis.AsyncConnectionPool.from_url') as mock_from_url:
            mock_from_url.side_effect = Exception("Connection error")

            with pytest.raises(ValueError, match="Failed to create Redis connection pool"):
                await async_storage.create_redis_engine()

    @pytest.mark.asyncio
    async def test_create_redis_session_async(self, async_storage):
        """Test creating async Redis session."""
        from redis.asyncio import ConnectionPool as AsyncConnectionPool
        mock_pool = AsyncMock(spec=AsyncConnectionPool)
        async_storage._redis_pool = mock_pool

        with patch('trpc_agent_sdk.storage._redis.AsyncRedis') as mock_redis:
            mock_redis.return_value = MagicMock()
            session = await async_storage.create_redis_session()

            mock_redis.assert_called_once_with(connection_pool=mock_pool)
            assert session is not None

    @pytest.mark.asyncio
    async def test_create_redis_session_sync(self, sync_storage):
        """Test creating sync Redis session."""
        mock_pool = MagicMock()
        sync_storage._redis_pool = mock_pool

        with patch('trpc_agent_sdk.storage._redis.SyncRedis') as mock_redis:
            mock_redis.return_value = MagicMock()
            session = await sync_storage.create_redis_session()

            mock_redis.assert_called_once_with(connection_pool=mock_pool)
            assert session is not None

    @pytest.mark.asyncio
    async def test_create_redis_session_no_pool(self, async_storage):
        """Test creating session without pool raises error."""
        async_storage._redis_pool = None

        with patch.object(async_storage, 'create_redis_engine') as mock_create:
            mock_create.return_value = None

            with pytest.raises(ValueError, match="Redis connection pool not initialized"):
                await async_storage.create_redis_session()

    def test_create_db_session(self, async_storage):
        """Test creating database session context manager."""
        ctx = async_storage.create_db_session()
        assert isinstance(ctx, RedisAsyncContextManager)
        assert ctx.redis_storage is async_storage

    def test_serialize_value_primitives(self, sync_storage):
        """Test serializing primitive values."""
        assert sync_storage._serialize_value("test") == "test"
        assert sync_storage._serialize_value(123) == "123"
        assert sync_storage._serialize_value(45.67) == "45.67"
        assert sync_storage._serialize_value(True) == "True"
        assert sync_storage._serialize_value(False) == "False"

    def test_serialize_value_complex(self, sync_storage):
        """Test serializing complex values."""
        data = {"key": "value", "count": 42}
        result = sync_storage._serialize_value(data)
        assert json.loads(result) == data

        data = ["item1", "item2", 3]
        result = sync_storage._serialize_value(data)
        assert json.loads(result) == data

    def test_deserialize_value_none(self, sync_storage):
        """Test deserializing None value."""
        assert sync_storage._deserialize_value(None) is None

    def test_deserialize_value_json(self, sync_storage):
        """Test deserializing JSON values."""
        data = {"key": "value", "count": 42}
        serialized = json.dumps(data).encode('utf-8')
        result = sync_storage._deserialize_value(serialized)
        assert result == data

    def test_deserialize_value_string(self, sync_storage):
        """Test deserializing string values."""
        result = sync_storage._deserialize_value(b"simple string")
        assert result == "simple string"

    def test_deserialize_value_unicode_error(self, sync_storage):
        """Test deserializing with unicode error."""
        # Invalid UTF-8 bytes
        invalid_bytes = b'\xff\xfe'
        result = sync_storage._deserialize_value(invalid_bytes)
        assert result == invalid_bytes

    @pytest.mark.asyncio
    async def test_add_valid_method(self, async_storage):
        """Test adding data with valid method."""
        mock_conn = AsyncMock()
        command = RedisCommand(method='set', args=('key', 'value'))

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.return_value = None
            await async_storage.add(mock_conn, command)
            mock_execute.assert_called_once_with(mock_conn, command)

    @pytest.mark.asyncio
    async def test_add_invalid_method(self, async_storage):
        """Test adding data with invalid method."""
        mock_conn = AsyncMock()
        command = RedisCommand(method='get', args=('key', ))

        with pytest.raises(ValueError, match="Invalid Redis set method"):
            await async_storage.add(mock_conn, command)

    @pytest.mark.asyncio
    async def test_add_all_expire_methods(self, async_storage):
        """Test all valid EXPIRE methods."""
        mock_conn = AsyncMock()

        for method in EXPIRE_METHOD:
            command = RedisCommand(method=method, args=('key', 'value'))
            with patch.object(async_storage, 'execute_command') as mock_execute:
                mock_execute.return_value = None
                await async_storage.add(mock_conn, command)
                mock_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_async(self, async_storage):
        """Test deleting data with async connection."""
        from redis.asyncio import Redis as AsyncRedis

        mock_conn = AsyncMock(spec=AsyncRedis)
        mock_conn.delete = AsyncMock()

        await async_storage.delete(mock_conn, 'test_key')
        mock_conn.delete.assert_called_once_with('test_key')

    @pytest.mark.asyncio
    async def test_delete_sync(self, sync_storage):
        """Test deleting data with sync connection."""
        mock_conn = MagicMock()
        mock_conn.delete = MagicMock(return_value=1)

        await sync_storage.delete(mock_conn, 'test_key')
        mock_conn.delete.assert_called_once_with('test_key')

    @pytest.mark.asyncio
    async def test_get_valid_method(self, async_storage):
        """Test getting data with valid method."""
        mock_conn = AsyncMock()
        command = RedisCommand(method='get', args=('key', ))

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.return_value = b'value'
            result = await async_storage.get(mock_conn, command)
            assert result == b'value'

    @pytest.mark.asyncio
    async def test_get_invalid_method(self, async_storage):
        """Test getting data with invalid method."""
        mock_conn = AsyncMock()
        command = RedisCommand(method='set', args=('key', 'value'))

        with pytest.raises(ValueError, match="Invalid Redis get method"):
            await async_storage.get(mock_conn, command)

    @pytest.mark.asyncio
    async def test_get_valid_methods(self, async_storage):
        """Test various valid get methods."""
        mock_conn = AsyncMock()
        # Only methods containing 'get' are valid
        valid_methods = ['get', 'hget', 'hgetall', 'mget']

        for method in valid_methods:
            command = RedisCommand(method=method, args=('key', ))
            with patch.object(async_storage, 'execute_command') as mock_execute:
                mock_execute.return_value = "value"
                result = await async_storage.get(mock_conn, command)
                assert result == "value"

    @pytest.mark.asyncio
    async def test_query_string_type(self, async_storage):
        """Test querying string type data."""
        mock_conn = AsyncMock()
        conditions = RedisCondition(limit=-1)

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.side_effect = [
                ['key1', 'key2'],  # keys command
                b'string',  # type command for key1
                b'value1',  # get command for key1
                b'string',  # type command for key2
                b'value2',  # get command for key2
            ]

            results = await async_storage.query(mock_conn, 'test:*', conditions)

            assert len(results) == 2
            assert results[0] == ('key1', 'value1')
            assert results[1] == ('key2', 'value2')

    @pytest.mark.asyncio
    async def test_query_hash_type(self, async_storage):
        """Test querying hash type data."""
        mock_conn = AsyncMock()
        conditions = RedisCondition(limit=-1)

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.side_effect = [
                ['hash_key'],  # keys command
                b'hash',  # type command
                {
                    'field': 'value'
                },  # hgetall command
            ]

            results = await async_storage.query(mock_conn, 'hash:*', conditions)

            assert len(results) == 1
            assert results[0] == ('hash_key', {'field': 'value'})

    @pytest.mark.asyncio
    async def test_query_list_type(self, async_storage):
        """Test querying list type data."""
        mock_conn = AsyncMock()
        conditions = RedisCondition(limit=-1)

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.side_effect = [
                ['list_key'],  # keys command
                b'list',  # type command
                ['item1', 'item2'],  # lrange command
            ]

            results = await async_storage.query(mock_conn, 'list:*', conditions)

            assert len(results) == 1
            assert results[0] == ('list_key', ['item1', 'item2'])

    @pytest.mark.asyncio
    async def test_query_set_type(self, async_storage):
        """Test querying set type data."""
        mock_conn = AsyncMock()
        conditions = RedisCondition(limit=-1)

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.side_effect = [
                ['set_key'],  # keys command
                b'set',  # type command
                {'member1', 'member2'},  # smembers command
            ]

            results = await async_storage.query(mock_conn, 'set:*', conditions)

            assert len(results) == 1
            assert results[0] == ('set_key', {'member1', 'member2'})

    @pytest.mark.asyncio
    async def test_query_zset_type(self, async_storage):
        """Test querying sorted set type data."""
        mock_conn = AsyncMock()
        conditions = RedisCondition(limit=-1)

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.side_effect = [
                ['zset_key'],  # keys command
                b'zset',  # type command
                [('member1', 1.0), ('member2', 2.0)],  # zrange command
            ]

            results = await async_storage.query(mock_conn, 'zset:*', conditions)

            assert len(results) == 1
            assert results[0] == ('zset_key', [('member1', 1.0), ('member2', 2.0)])

    @pytest.mark.asyncio
    async def test_query_with_limit(self, async_storage):
        """Test querying with limit."""
        mock_conn = AsyncMock()
        conditions = RedisCondition(limit=2)

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.side_effect = [
                ['key1', 'key2', 'key3'],  # keys command (3 keys)
                b'string',  # type for key1
                b'value1',  # get for key1
                b'string',  # type for key2
                b'value2',  # get for key2
            ]

            results = await async_storage.query(mock_conn, 'test:*', conditions)

            # Should only process 2 keys due to limit
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_with_error(self, async_storage):
        """Test querying with error handling."""
        mock_conn = AsyncMock()
        conditions = RedisCondition(limit=-1)

        with patch.object(async_storage, 'execute_command') as mock_execute:
            mock_execute.side_effect = [
                ['key1', 'key2'],  # keys command
                Exception("Error"),  # type command fails
                b'string',  # type for key2
                b'value2',  # get for key2
            ]

            results = await async_storage.query(mock_conn, 'test:*', conditions)

            # Should skip key1 and continue with key2
            assert len(results) == 1
            assert results[0] == ('key2', 'value2')

    @pytest.mark.asyncio
    async def test_commit(self, async_storage):
        """Test commit operation (no-op for Redis)."""
        mock_conn = AsyncMock()
        await async_storage.commit(mock_conn)
        # Should not raise any error

    @pytest.mark.asyncio
    async def test_refresh(self, async_storage):
        """Test refresh operation (no-op for Redis)."""
        mock_conn = AsyncMock()
        await async_storage.refresh(mock_conn, None)
        # Should not raise any error

    @pytest.mark.asyncio
    async def test_close_async(self, async_storage):
        """Test closing async Redis connection pool."""
        from redis.asyncio import ConnectionPool as AsyncConnectionPool

        mock_pool = AsyncMock(spec=AsyncConnectionPool)
        mock_pool.disconnect = AsyncMock()
        async_storage._redis_pool = mock_pool

        await async_storage.close()
        mock_pool.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_sync(self, sync_storage):
        """Test closing sync Redis connection pool."""
        mock_pool = MagicMock()
        mock_pool.disconnect = MagicMock(return_value=None)
        sync_storage._redis_pool = mock_pool

        await sync_storage.close()
        mock_pool.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_no_pool(self, async_storage):
        """Test closing when no pool exists."""
        async_storage._redis_pool = None
        await async_storage.close()
        # Should not raise any error

    @pytest.mark.asyncio
    async def test_close_with_error(self, async_storage):
        """Test closing with error."""
        from redis.asyncio import ConnectionPool as AsyncConnectionPool

        mock_pool = AsyncMock(spec=AsyncConnectionPool)
        mock_pool.disconnect = AsyncMock(side_effect=Exception("Disconnect error"))
        async_storage._redis_pool = mock_pool

        await async_storage.close()
        # Should not raise error, just log

    @pytest.mark.asyncio
    async def test_getattr_dynamic_command(self, async_storage):
        """Test dynamic command method access."""
        mock_pool = MagicMock()
        async_storage._redis_pool = mock_pool

        with patch.object(async_storage, 'create_db_session') as mock_ctx:
            mock_conn = AsyncMock()
            mock_ctx.return_value.__aenter__.return_value = mock_conn
            mock_ctx.return_value.__aexit__.return_value = None

            with patch.object(async_storage, 'execute_command') as mock_execute:
                mock_execute.return_value = "result"

                # Test dynamic method call
                result = await async_storage.custom_command('arg1', kwarg1='value1')

                assert result == "result"
                mock_execute.assert_called_once()
                call_args = mock_execute.call_args[0]
                command = call_args[1]
                assert command.method == 'custom_command'
                assert command.args == ('arg1', )
                assert command.kwargs == {'kwarg1': 'value1'}

    @pytest.mark.asyncio
    async def test_getattr_no_pool(self, async_storage):
        """Test dynamic method call without pool."""
        async_storage._redis_pool = None

        # Should return None when no pool
        result = await async_storage.some_command('arg')
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_command_with_method(self, async_storage):
        """Test executing command with existing method."""
        mock_conn = AsyncMock()
        mock_conn.set = AsyncMock(return_value="OK")

        command = RedisCommand(method='set', args=('key', 'value'), expire=RedisExpire(ttl=Ttl(enable=False)))

        result = await async_storage.execute_command(mock_conn, command)

        assert result == "OK"
        mock_conn.set.assert_called_once_with('key', 'value')

    @pytest.mark.asyncio
    async def test_execute_command_without_method(self, async_storage):
        """Test executing command without existing method."""
        mock_conn = AsyncMock()
        mock_conn.execute_command = AsyncMock(return_value="OK")

        command = RedisCommand(method='custom', args=('key', 'value'), expire=RedisExpire(ttl=Ttl(enable=False)))

        with patch.object(mock_conn, 'custom', None):
            result = await async_storage.execute_command(mock_conn, command)

            assert result == "OK"
            mock_conn.execute_command.assert_called_once_with('CUSTOM', 'key', 'value')

    @pytest.mark.asyncio
    async def test_execute_command_with_expire(self, async_storage):
        """Test executing command with TTL expiration."""
        mock_conn = AsyncMock()
        mock_conn.set = AsyncMock(return_value="OK")

        command = RedisCommand(method='set',
                               args=('key', 'value'),
                               expire=RedisExpire(ttl=Ttl(enable=True, ttl_seconds=60)))

        with patch.object(async_storage, 'expire') as mock_expire:
            mock_expire.return_value = None
            result = await async_storage.execute_command(mock_conn, command)

            assert result == "OK"
            mock_expire.assert_called_once()
            # Check that expire was called with correct key
            expire_command = mock_expire.call_args[0][1]
            assert expire_command.key == 'key'

    @pytest.mark.asyncio
    async def test_execute_command_sync(self, sync_storage):
        """Test executing command with sync connection."""
        mock_conn = MagicMock()
        mock_conn.set = MagicMock(return_value="OK")

        command = RedisCommand(method='set', args=('key', 'value'), expire=RedisExpire(ttl=Ttl(enable=False)))

        result = await sync_storage.execute_command(mock_conn, command)

        assert result == "OK"
        mock_conn.set.assert_called_once_with('key', 'value')

    @pytest.mark.asyncio
    async def test_expire_disabled(self, async_storage):
        """Test expire when TTL is disabled."""
        mock_conn = AsyncMock()
        expire_command = RedisExpire(ttl=Ttl(enable=False))

        result = await async_storage.expire(mock_conn, expire_command)
        assert result is None

    @pytest.mark.asyncio
    async def test_expire_with_args(self, async_storage):
        """Test expire with explicit args."""
        mock_conn = AsyncMock()
        mock_conn.expire = AsyncMock(return_value=1)

        expire_command = RedisExpire(key='test_key',
                                     method='expire',
                                     ttl=Ttl(enable=True, ttl_seconds=60),
                                     args=('test_key', 60))

        result = await async_storage.expire(mock_conn, expire_command)

        assert result == 1
        mock_conn.expire.assert_called_once_with('test_key', 60)

    @pytest.mark.asyncio
    async def test_expire_without_args(self, async_storage):
        """Test expire without explicit args."""
        mock_conn = AsyncMock()
        mock_conn.expire = AsyncMock(return_value=1)

        expire_command = RedisExpire(key='test_key', method='expire', ttl=Ttl(enable=True, ttl_seconds=120))

        result = await async_storage.expire(mock_conn, expire_command)

        assert result == 1
        mock_conn.expire.assert_called_once_with('test_key', 120)

    @pytest.mark.asyncio
    async def test_expire_without_method(self, async_storage):
        """Test expire using execute_command."""
        mock_conn = AsyncMock()
        mock_conn.execute_command = AsyncMock(return_value=1)

        expire_command = RedisExpire(
            key='test_key',
            method='pexpire',  # Method that doesn't exist
            ttl=Ttl(enable=True, ttl_seconds=60))

        with patch.object(mock_conn, 'pexpire', None):
            result = await async_storage.expire(mock_conn, expire_command)

            assert result == 1
            mock_conn.execute_command.assert_called_once_with('PEXPIRE', 'test_key', 60)

    @pytest.mark.asyncio
    async def test_expire_sync_connection(self, sync_storage):
        """Test expire with sync connection."""
        mock_conn = MagicMock()
        mock_conn.expire = MagicMock(return_value=1)

        expire_command = RedisExpire(key='test_key', method='expire', ttl=Ttl(enable=True, ttl_seconds=60))

        result = await sync_storage.expire(mock_conn, expire_command)

        assert result == 1
        mock_conn.expire.assert_called_once_with('test_key', 60)


class TestRedisAsyncContextManager:
    """Test suite for RedisAsyncContextManager class."""

    @pytest.mark.asyncio
    async def test_context_manager_async(self):
        """Test async context manager with async Redis."""
        from redis.asyncio import Redis as AsyncRedis

        mock_storage = MagicMock()
        mock_session = AsyncMock(spec=AsyncRedis)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_storage.create_redis_session = AsyncMock(return_value=mock_session)

        ctx = RedisAsyncContextManager(mock_storage)

        async with ctx as session:
            assert session is mock_session
            mock_session.__aenter__.assert_called_once()

        mock_session.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_sync(self):
        """Test async context manager with sync Redis."""
        mock_storage = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock()

        mock_storage.create_redis_session = AsyncMock(return_value=mock_session)

        ctx = RedisAsyncContextManager(mock_storage)

        async with ctx as session:
            assert session is mock_session
            mock_session.__enter__.assert_called_once()

        mock_session.__exit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_no_session(self):
        """Test context manager exit without session."""
        mock_storage = MagicMock()
        mock_storage.create_redis_session = AsyncMock(return_value=None)

        ctx = RedisAsyncContextManager(mock_storage)
        ctx._session = None

        # Should not raise error
        await ctx.__aexit__(None, None, None)


class TestRedisDataClasses:
    """Test suite for Redis data classes."""

    def test_redis_expire_default(self):
        """Test RedisExpire with default values."""
        expire = RedisExpire()

        assert expire.key == ""
        assert expire.method == "expire"
        assert isinstance(expire.ttl, Ttl)
        assert expire.args == ()
        assert expire.kwargs == {}

    def test_redis_expire_custom(self):
        """Test RedisExpire with custom values."""
        ttl = Ttl(enable=True, ttl_seconds=120)
        expire = RedisExpire(key="test_key", method="pexpire", ttl=ttl, args=("arg1", ), kwargs={"kw1": "val1"})

        assert expire.key == "test_key"
        assert expire.method == "pexpire"
        assert expire.ttl.ttl_seconds == 120
        assert expire.args == ("arg1", )
        assert expire.kwargs == {"kw1": "val1"}

    def test_redis_command_default(self):
        """Test RedisCommand with default values."""
        command = RedisCommand(method="set")

        assert command.method == "set"
        assert isinstance(command.expire, RedisExpire)
        assert command.args == ()
        assert command.kwargs == {}

    def test_redis_command_custom(self):
        """Test RedisCommand with custom values."""
        expire = RedisExpire(key="test_key", ttl=Ttl(enable=True, ttl_seconds=60))
        command = RedisCommand(method="hset", expire=expire, args=("hash_key", "field", "value"), kwargs={"nx": True})

        assert command.method == "hset"
        assert command.expire.key == "test_key"
        assert command.args == ("hash_key", "field", "value")
        assert command.kwargs == {"nx": True}

    def test_redis_condition_default(self):
        """Test RedisCondition with default values."""
        condition = RedisCondition()

        assert condition.limit == -1

    def test_redis_condition_custom(self):
        """Test RedisCondition with custom limit."""
        condition = RedisCondition(limit=100)

        assert condition.limit == 100


class TestExpireMethods:
    """Test suite for EXPIRE_METHOD constant."""

    def test_expire_method_list(self):
        """Test EXPIRE_METHOD contains expected methods."""
        expected_methods = ['set', 'hset', 'hmset', 'lpush', 'rpush', 'sadd', 'zadd']

        assert EXPIRE_METHOD == expected_methods

    def test_expire_method_immutable(self):
        """Test EXPIRE_METHOD list contents."""
        # Verify all expected methods are present
        for method in ['set', 'hset', 'hmset', 'lpush', 'rpush', 'sadd', 'zadd']:
            assert method in EXPIRE_METHOD


class TestRedisStorageIntegration:
    """Integration tests for RedisStorage."""

    @pytest.mark.asyncio
    async def test_full_workflow_async(self):
        """Test complete workflow with async storage."""
        from redis.asyncio import ConnectionPool as AsyncConnectionPool
        from redis.asyncio import Redis as AsyncRedis

        storage = RedisStorage(redis_url="redis://localhost:6379/0", is_async=True)

        # Create pool instance without calling __init__ to avoid real connections
        mock_pool = AsyncConnectionPool.__new__(AsyncConnectionPool)
        mock_pool.disconnect = AsyncMock()
        storage._redis_pool = mock_pool

        # Create connection instance without calling __init__
        mock_conn = AsyncRedis.__new__(AsyncRedis)
        mock_conn.set = AsyncMock(return_value="OK")
        mock_conn.get = AsyncMock(return_value=b"value")
        mock_conn.delete = AsyncMock(return_value=1)
        mock_conn.expire = AsyncMock(return_value=1)

        # Add data with TTL
        command = RedisCommand(method='set',
                               args=('key', 'value'),
                               expire=RedisExpire(ttl=Ttl(enable=True, ttl_seconds=60)))
        await storage.add(mock_conn, command)
        mock_conn.set.assert_called_once_with('key', 'value')
        mock_conn.expire.assert_called_once()

        # Get data
        get_command = RedisCommand(method='get', args=('key', ))
        result = await storage.get(mock_conn, get_command)
        assert result == b"value"
        mock_conn.get.assert_called_once_with('key')

        # Delete data
        await storage.delete(mock_conn, 'key')
        mock_conn.delete.assert_called_once_with('key')

        # Close
        await storage.close()
        mock_pool.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_workflow(self):
        """Test workflow using context manager."""
        from redis.asyncio import ConnectionPool as AsyncConnectionPool
        from redis.asyncio import Redis as AsyncRedis

        storage = RedisStorage(redis_url="redis://localhost:6379/0", is_async=True)

        # Create pool instance without calling __init__
        mock_pool = AsyncConnectionPool.__new__(AsyncConnectionPool)
        mock_pool.disconnect = AsyncMock()
        storage._redis_pool = mock_pool

        # Create connection instance without calling __init__
        mock_conn = AsyncRedis.__new__(AsyncRedis)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_conn.set = AsyncMock(return_value="OK")

        # Patch create_redis_session to return the mock connection instance
        with patch.object(storage, 'create_redis_session', return_value=mock_conn):
            async with storage.create_db_session() as conn:
                assert conn is mock_conn

                command = RedisCommand(method='set', args=('key', 'value'), expire=RedisExpire(ttl=Ttl(enable=False)))
                await storage.execute_command(conn, command)

                # Verify set was called
                mock_conn.set.assert_called_once_with('key', 'value')

            # Verify context manager methods were called
            mock_conn.__aenter__.assert_called()
            mock_conn.__aexit__.assert_called()
