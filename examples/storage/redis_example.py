#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Redis Storage Example

This example demonstrates how to use RedisStorage with various Redis operations.
It covers all the main interfaces and Redis commands.
"""

# System modules
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional

# Add the project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Local modules
from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisCondition
from trpc_agent_sdk.storage import RedisExpire
from trpc_agent_sdk.storage import RedisStorage


class RedisExampleManager:
    """Redis example manager class."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        """Initialize Redis example manager."""
        self.redis_storage = RedisStorage(redis_url=redis_url, is_async=True)
        self.test_data = {
            "user:1": {
                "name": "Alice",
                "age": 30,
                "email": "alice@example.com"
            },
            "user:2": {
                "name": "Bob",
                "age": 25,
                "email": "bob@example.com"
            },
            "user:3": {
                "name": "Charlie",
                "age": 35,
                "email": "charlie@example.com"
            }
        }

    async def setup_redis(self) -> None:
        """Setup Redis connection."""
        print("=== Setting up Redis Connection ===")
        await self.redis_storage.create_redis_engine()
        print("Redis connection pool created successfully!")

    async def string_operations_example(self) -> None:
        """Demonstrate string operations."""
        print("\n=== String Operations Example ===")

        async with self.redis_storage.create_db_session() as conn:
            # Set string values
            for key, value in self.test_data.items():
                command = RedisCommand(
                    method="set",
                    args=(key, json.dumps(value)),
                    expire=RedisExpire(time=3600)  # Expire in 1 hour
                )
                await self.redis_storage.add(conn, command)
                print(f"Set {key}: {value}")

            # Get string values
            for key in self.test_data.keys():
                get_command = RedisCommand(method="get", args=(key, ))
                result = await self.redis_storage.get(conn, get_command)
                if result:
                    data = json.loads(result.decode('utf-8'))
                    print(f"Get {key}: {data}")

    async def hash_operations_example(self) -> None:
        """Demonstrate hash operations."""
        print("\n=== Hash Operations Example ===")

        async with self.redis_storage.create_db_session() as conn:
            # Set hash values
            hash_key = "user_profiles"
            for user_id, user_data in self.test_data.items():
                for field, value in user_data.items():
                    command = RedisCommand(
                        method="hset",
                        args=(f"{hash_key}:{user_id}", field, str(value)),
                        expire=RedisExpire(time=7200)  # Expire in 2 hours
                    )
                    await self.redis_storage.add(conn, command)

            print(f"Set hash data for {len(self.test_data)} users")

            # Get hash values
            for user_id in self.test_data.keys():
                get_command = RedisCommand(method="hgetall", args=(f"{hash_key}:{user_id}", ))
                result = await self.redis_storage.get(conn, get_command)
                if result:
                    print(f"Hash data for {user_id}: {result}")

    async def list_operations_example(self) -> None:
        """Demonstrate list operations."""
        print("\n=== List Operations Example ===")

        async with self.redis_storage.create_db_session() as conn:
            list_key = "user_activities"

            # Push items to list
            activities = [
                "User Alice logged in", "User Bob updated profile", "User Charlie posted message",
                "User Alice logged out"
            ]

            for activity in activities:
                command = RedisCommand(
                    method="rpush",
                    args=(list_key, activity),
                    expire=RedisExpire(time=1800)  # Expire in 30 minutes
                )
                await self.redis_storage.add(conn, command)

            print(f"Added {len(activities)} activities to list")

            # Get list items
            get_command = RedisCommand(method="lrange", args=(list_key, 0, -1))
            result = await self.redis_storage.execute_command(conn, get_command)
            if result:
                print(f"List activities: {result}")

    async def set_operations_example(self) -> None:
        """Demonstrate set operations."""
        print("\n=== Set Operations Example ===")

        async with self.redis_storage.create_db_session() as conn:
            set_key = "active_users"

            # Add members to set
            users = ["alice", "bob", "charlie", "david", "eve"]
            for user in users:
                command = RedisCommand(
                    method="sadd",
                    args=(set_key, user),
                    expire=RedisExpire(time=900)  # Expire in 15 minutes
                )
                await self.redis_storage.add(conn, command)

            print(f"Added {len(users)} users to set")

            # Get set members
            get_command = RedisCommand(method="smembers", args=(set_key, ))
            result = await self.redis_storage.execute_command(conn, get_command)
            if result:
                print(f"Set members: {result}")

    async def sorted_set_operations_example(self) -> None:
        """Demonstrate sorted set operations."""
        print("\n=== Sorted Set Operations Example ===")

        async with self.redis_storage.create_db_session() as conn:
            zset_key = "user_scores"

            # Add scored members to sorted set
            scores = {"alice": 100, "bob": 85, "charlie": 92, "david": 78, "eve": 95}
            for member, score in scores.items():
                command = RedisCommand(
                    method="zadd",
                    args=(zset_key, {
                        member: score
                    }),
                    expire=RedisExpire(time=3600)  # Expire in 1 hour
                )
                await self.redis_storage.add(conn, command)

            print(f"Added {len(scores)} scored members to sorted set")

            # Get sorted set members with scores
            get_command = RedisCommand(method="zrevrange", args=(zset_key, 0, -1), kwargs={"withscores": True})
            result = await self.redis_storage.execute_command(conn, get_command)
            if result:
                print(f"Sorted set (desc): {result}")

    async def query_operations_example(self) -> None:
        """Demonstrate query operations using RedisStorage.query() method."""
        print("\n=== Query Operations Example ===")

        async with self.redis_storage.create_db_session() as conn:
            # Use RedisStorage.query() method to query keys with pattern
            condition = RedisCondition(limit=5)  # Limit to 5 results
            results = await self.redis_storage.query(conn, "user:*", condition)

            print(f"Query results for 'user:*' pattern:")
            print(f"Found {len(results)} items")

            for i, result in enumerate(results, 1):
                print(f"  Result {i}: {result}")

            # Query with different patterns and limits
            print("\n--- Query hash keys ---")
            hash_condition = RedisCondition(limit=3)
            hash_results = await self.redis_storage.query(conn, "user_profiles:*", hash_condition)
            print(f"Hash query results: {len(hash_results)} items")
            for i, result in enumerate(hash_results, 1):
                print(f"  Hash {i}: {result}")

            # Query list keys
            print("\n--- Query list keys ---")
            list_condition = RedisCondition(limit=2)
            list_results = await self.redis_storage.query(conn, "user_activities", list_condition)
            print(f"List query results: {len(list_results)} items")
            for i, result in enumerate(list_results, 1):
                print(f"  List item {i}: {result}")

            # Query set keys
            print("\n--- Query set keys ---")
            set_condition = RedisCondition(limit=1)
            set_results = await self.redis_storage.query(conn, "active_users", set_condition)
            print(f"Set query results: {len(set_results)} items")
            for i, result in enumerate(set_results, 1):
                print(f"  Set {i}: {result}")

            # Query sorted set keys
            print("\n--- Query sorted set keys ---")
            zset_condition = RedisCondition(limit=1)
            zset_results = await self.redis_storage.query(conn, "user_scores", zset_condition)
            print(f"Sorted set query results: {len(zset_results)} items")
            for i, result in enumerate(zset_results, 1):
                print(f"  Sorted set {i}: {result}")

            # Query all keys with no limit
            print("\n--- Query all keys (no limit) ---")
            all_condition = RedisCondition(limit=-1)  # No limit
            all_results = await self.redis_storage.query(conn, "*", all_condition)
            print(f"Total keys found: {len(all_results)}")

            # Show first few results
            for i, result in enumerate(all_results[:3], 1):
                print(f"  All keys result {i}: {result}")

            if len(all_results) > 3:
                print(f"  ... and {len(all_results) - 3} more results")

            print(f"\nTotal query operations completed successfully!")

    async def delete_operations_example(self) -> None:
        """Demonstrate delete operations."""
        print("\n=== Delete Operations Example ===")

        async with self.redis_storage.create_db_session() as conn:
            # Delete specific keys
            keys_to_delete = ["user:1", "user_profiles:user:1"]
            for key in keys_to_delete:
                await self.redis_storage.delete(conn, key)
                print(f"Deleted key: {key}")

    async def dynamic_command_example(self) -> None:
        """Demonstrate dynamic command execution."""
        print("\n=== Dynamic Command Example ===")

        # Use dynamic method calls
        async with self.redis_storage.create_db_session() as conn:
            await self.redis_storage.set("dynamic_key", "dynamic_value", ex=300)
            get_command = RedisCommand(method="get", args=("dynamic_key", ))
            result = await self.redis_storage.get(conn, get_command)
            if result:
                print(f"Dynamic command result: {result.decode('utf-8')}")

            # Get Redis info
            info = await self.redis_storage.info()
            if info:
                print(f"Redis server info (memory): {info.get('used_memory_human', 'N/A')}")

    async def transaction_example(self) -> None:
        """Demonstrate transaction-like operations."""
        print("\n=== Transaction Example ===")

        async with self.redis_storage.create_db_session() as conn:
            # Simulate transaction with multiple operations
            operations = [
                RedisCommand(method="set", args=("tx:key1", "value1")),
                RedisCommand(method="set", args=("tx:key2", "value2")),
                RedisCommand(method="set", args=("tx:key3", "value3"))
            ]

            for op in operations:
                await self.redis_storage.add(conn, op)

            # Commit (Redis operations are atomic by default)
            await self.redis_storage.commit(conn)
            print("Transaction completed successfully")

    async def advanced_query_example(self) -> None:
        """Demonstrate advanced query operations with RedisStorage.query() method."""
        print("\n=== Advanced Query Example ===")

        async with self.redis_storage.create_db_session() as conn:
            # First, create some test data for querying
            test_keys = {
                "product:1": {
                    "name": "Laptop",
                    "price": 999.99,
                    "category": "Electronics"
                },
                "product:2": {
                    "name": "Mouse",
                    "price": 29.99,
                    "category": "Electronics"
                },
                "product:3": {
                    "name": "Book",
                    "price": 19.99,
                    "category": "Education"
                },
                "order:1001": {
                    "user_id": 1,
                    "total": 1029.98,
                    "status": "completed"
                },
                "order:1002": {
                    "user_id": 2,
                    "total": 19.99,
                    "status": "pending"
                },
                "session:abc123": {
                    "user_id": 1,
                    "login_time": "2025-01-01T10:00:00"
                },
                "session:def456": {
                    "user_id": 2,
                    "login_time": "2025-01-01T11:00:00"
                }
            }

            # Add test data
            for key, value in test_keys.items():
                command = RedisCommand(
                    method="set",
                    args=(key, json.dumps(value)),
                    expire=RedisExpire(time=1800)  # 30 minutes
                )
                await self.redis_storage.add(conn, command)

            print(f"Added {len(test_keys)} test items for querying")

            # Query 1: Get all products
            print("\n--- Query 1: All products ---")
            product_condition = RedisCondition(limit=-1)  # No limit
            products = await self.redis_storage.query(conn, "product:*", product_condition)
            print(f"Found {len(products)} products:")
            for i, product in enumerate(products, 1):
                print(f"  Product {i}: {product}")

            # Query 2: Get limited orders
            print("\n--- Query 2: Orders (limited to 1) ---")
            order_condition = RedisCondition(limit=1)
            orders = await self.redis_storage.query(conn, "order:*", order_condition)
            print(f"Found {len(orders)} order(s):")
            for i, order in enumerate(orders, 1):
                print(f"  Order {i}: {order}")

            # Query 3: Get all sessions
            print("\n--- Query 3: All sessions ---")
            session_condition = RedisCondition(limit=10)  # Limit to 10
            sessions = await self.redis_storage.query(conn, "session:*", session_condition)
            print(f"Found {len(sessions)} session(s):")
            for i, session in enumerate(sessions, 1):
                print(f"  Session {i}: {session}")

            # Query 4: Pattern matching with wildcards
            print("\n--- Query 4: Pattern matching ---")
            # Query keys that start with any letter followed by numbers
            pattern_condition = RedisCondition(limit=5)
            pattern_results = await self.redis_storage.query(conn, "*:*", pattern_condition)
            print(f"Found {len(pattern_results)} items matching '*:*' pattern:")
            for i, result in enumerate(pattern_results, 1):
                print(f"  Pattern result {i}: {result}")

            # Query 5: Empty result handling
            print("\n--- Query 5: Non-existent pattern ---")
            empty_condition = RedisCondition(limit=5)
            empty_results = await self.redis_storage.query(conn, "nonexistent:*", empty_condition)
            print(f"Found {len(empty_results)} items for non-existent pattern")

            # Clean up test data
            print("\n--- Cleaning up test data ---")
            for key in test_keys.keys():
                await self.redis_storage.delete(conn, key)
            print("Test data cleaned up successfully")

    async def cleanup_example(self) -> None:
        """Clean up test data."""
        print("\n=== Cleanup Example ===")

        async with self.redis_storage.create_db_session() as conn:
            # Get all test keys
            all_keys_command = RedisCommand(method="keys", args=("*", ))
            all_keys = await self.redis_storage.execute_command(conn, all_keys_command)

            if all_keys:
                print(f"Found {len(all_keys)} keys to clean up")
                for key in all_keys:
                    if isinstance(key, bytes):
                        key = key.decode('utf-8')
                    await self.redis_storage.delete(conn, key)
                print("Cleanup completed")
            else:
                print("No keys found for cleanup")

    async def close_connection(self) -> None:
        """Close Redis connection."""
        print("\n=== Closing Connection ===")
        await self.redis_storage.close()
        print("Redis connection closed!")


class RedisConfig:
    """Redis configuration class."""

    # Default Redis settings
    DEFAULT_HOST = "localhost"
    DEFAULT_PORT = 6379
    DEFAULT_DB = 0
    DEFAULT_PASSWORD = "test"
    DEFAULT_DECODE_RESPONSES = True
    DEFAULT_MAX_CONNECTIONS = 10

    @classmethod
    def get_redis_url(cls,
                      host: Optional[str] = None,
                      port: Optional[int] = None,
                      db: Optional[int] = None,
                      password: Optional[str] = None) -> str:
        """Get Redis connection URL."""
        host = host or os.getenv("REDIS_HOST", cls.DEFAULT_HOST)
        port = port or int(os.getenv("REDIS_PORT", cls.DEFAULT_PORT))
        db = db or int(os.getenv("REDIS_DB", cls.DEFAULT_DB))
        password = password or os.getenv("REDIS_PASSWORD", cls.DEFAULT_PASSWORD)

        if password:
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"

    @classmethod
    def get_redis_kwargs(cls) -> Dict[str, Any]:
        """Get additional Redis connection parameters."""
        return {
            "decode_responses": cls.DEFAULT_DECODE_RESPONSES,
            "max_connections": int(os.getenv("REDIS_MAX_CONNECTIONS", cls.DEFAULT_MAX_CONNECTIONS)),
            "socket_timeout": int(os.getenv("REDIS_SOCKET_TIMEOUT", 5)),
            "socket_connect_timeout": int(os.getenv("REDIS_CONNECT_TIMEOUT", 5)),
            "retry_on_timeout": True,
            "health_check_interval": 30
        }


async def run_redis_example() -> None:
    """Run the complete Redis example."""
    # Redis connection URL - modify as needed
    # https://github.com/redis/redis-py/blob/master/redis/connection.py
    # AbstractConnection
    # https://github.com/redis/redis-py/blob/master/redis/asyncio/connection.py
    # AbstractConnection

    redis_url = RedisConfig.get_redis_url()

    example_manager = RedisExampleManager(redis_url)

    try:
        # Setup Redis connection
        await example_manager.setup_redis()

        # Run all examples
        await example_manager.string_operations_example()
        await example_manager.hash_operations_example()
        await example_manager.list_operations_example()
        await example_manager.set_operations_example()
        await example_manager.sorted_set_operations_example()
        await example_manager.query_operations_example()
        await example_manager.advanced_query_example()
        await example_manager.dynamic_command_example()
        await example_manager.transaction_example()
        await example_manager.delete_operations_example()

        # Optional: Clean up test data
        # await example_manager.cleanup_example()

    except Exception as e:
        print(f"Error during example execution: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Always close the connection
        await example_manager.close_connection()


if __name__ == "__main__":
    print("Starting Redis Storage Example...")
    asyncio.run(run_redis_example())
    print("Redis Storage Example completed!")
