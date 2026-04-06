# RedisStorage Usage Guide

This document explains in detail how to use the `RedisStorage` class for Redis database operations, including strings, hashes, lists, sets, sorted sets, and other common data types.

## Overview

`RedisStorage` is an async/sync Redis storage implementation based on redis-py, providing a unified interface for handling various Redis data operations.

## Core Components

### 1. RedisStorage Class
The primary storage class that provides Redis connection and operation interfaces.

### 2. Helper Classes
- `RedisCommand`: Used to construct Redis commands, e.g., SET, GET, HSET, etc.
- `RedisExpire`: Used for Redis expiration time operation commands, setting expiration policies via the `Ttl` object
- `RedisCondition`: A condition filtering class used for queries
- `Ttl`: Used to configure TTL (Time-To-Live) expiration time, containing fields such as `ttl_seconds` (expiration in seconds), `enable` (whether enabled), etc.

## Prerequisites

### 1. Install Required Dependencies

```bash
# Core dependencies
pip install redis

# Async support (optional, recommended)
pip install redis[hiredis]
```

### 2. Redis Server Setup

#### Start Redis with Docker
```bash
# Basic startup
docker run -d -p 6379:6379 redis:latest

# Start with password
docker run -d -p 6379:6379 redis:latest redis-server --requirepass mypassword

# Persistent data
docker run -d -p 6379:6379 -v redis-data:/data redis:latest redis-server --appendonly yes
```

#### Install Redis Locally
```bash
# Ubuntu/Debian
sudo apt-get install redis-server

# CentOS/RHEL
sudo yum install redis

# macOS
brew install redis

# Start service
redis-server
```

#### Redis Configuration Optimization
```bash
# Edit redis.conf
# Set maximum memory
maxmemory 2gb
maxmemory-policy allkeys-lru

# Enable persistence
save 900 1
save 300 10
save 60 10000

# Network configuration
bind 127.0.0.1
port 6379
timeout 300
```
## Basic Usage

### 1. Initialize RedisStorage

```python
from trpc_agent_sdk.storage import RedisStorage

# Async mode (recommended)
storage = RedisStorage(
    redis_url="redis://localhost:6379/0",
    is_async=True,
    decode_responses=True,
    max_connections=10,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30
)

# Sync mode
storage = RedisStorage(
    redis_url="redis://localhost:6379/0",
    is_async=False,
    decode_responses=True
)

# Connect with password
storage = RedisStorage(
    redis_url="redis://:password@localhost:6379/0",
    is_async=True
)
```

### 2. Define Redis Commands

```python
from trpc_agent_sdk.storage import RedisCommand, RedisExpire, RedisCondition
from trpc_agent_sdk.types import Ttl

# String operation command
set_command = RedisCommand(
    method="set",
    args=("user:1", "{'name': 'Alice', 'age': 30}"),
    expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))  # Expires after 1 hour
)

get_command = RedisCommand(
    method="get",
    args=("user:1",)
)

# Hash operation command
hset_command = RedisCommand(
    method="hset",
    args=("user_profile:1", "name", "Alice"),
    expire=RedisExpire(ttl=Ttl(ttl_seconds=7200))  # Expires after 2 hours
)

hgetall_command = RedisCommand(
    method="hgetall",
    args=("user_profile:1",)
)
```

### 3. Basic Operation Example

```python
import asyncio
import json
from trpc_agent_sdk.storage import RedisStorage, RedisCommand, RedisExpire, RedisCondition
from trpc_agent_sdk.types import Ttl

async def basic_example():
    # Initialize storage
    storage = RedisStorage(
        redis_url="redis://localhost:6379/0",
        is_async=True
    )

    try:
        # Create Redis connection pool
        await storage.create_redis_engine()

        # Use Redis session
        async with storage.create_db_session() as conn:
            # Set string value
            user_data = {"name": "Alice", "age": 30, "email": "alice@example.com"}
            set_command = RedisCommand(
                method="set",
                args=("user:1", json.dumps(user_data)),
                expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
            )
            await storage.add(conn, set_command)

            print("User data saved successfully")

            # Get string value
            get_command = RedisCommand(method="get", args=("user:1",))
            result = await storage.get(conn, get_command)

            if result:
                retrieved_data = json.loads(result.decode('utf-8'))
                print(f"Retrieved user: {retrieved_data}")

            # Query keys matching a pattern
            condition = RedisCondition(limit=10)
            keys = await storage.query(conn, "user:*", condition)
            print(f"Found {len(keys)} user keys")

            # Delete key
            await storage.delete(conn, "user:1")
            print("User data deleted")

    finally:
        await storage.close()

# Run example
asyncio.run(basic_example())
```

### 4. Configuration Management

```python
import os
from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class RedisConfig:
    """Redis configuration class"""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None

    # Connection pool settings
    max_connections: int = 10
    socket_timeout: int = 5
    socket_connect_timeout: int = 5
    retry_on_timeout: bool = True
    health_check_interval: int = 30

    # Redis settings
    decode_responses: bool = True

    def get_redis_url(self) -> str:
        """Get Redis connection URL"""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"

    def get_connection_kwargs(self) -> Dict[str, Any]:
        """Get connection parameters"""
        return {
            "decode_responses": self.decode_responses,
            "max_connections": self.max_connections,
            "socket_timeout": self.socket_timeout,
            "socket_connect_timeout": self.socket_connect_timeout,
            "retry_on_timeout": self.retry_on_timeout,
            "health_check_interval": self.health_check_interval,
        }

    @classmethod
    def from_env(cls) -> 'RedisConfig':
        """Create configuration from environment variables"""
        return cls(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            password=os.getenv("REDIS_PASSWORD"),
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "10")),
            socket_timeout=int(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
        )

# Usage
config = RedisConfig.from_env()
storage = RedisStorage(
    redis_url=config.get_redis_url(),
    is_async=True,
    **config.get_connection_kwargs()
)
```

### 5. Environment Variable Configuration

Set environment variables to configure the Redis connection:

```bash
# Basic connection configuration
export REDIS_HOST=localhost
export REDIS_PORT=6379
export REDIS_DB=0
export REDIS_PASSWORD=your_password

# Connection pool configuration
export REDIS_MAX_CONNECTIONS=20
export REDIS_SOCKET_TIMEOUT=10
export REDIS_CONNECT_TIMEOUT=5

# Other configuration
export REDIS_DECODE_RESPONSES=true
export REDIS_RETRY_ON_TIMEOUT=true
```

## RedisStorage Interface Details

### 1. Core Interfaces

#### Redis Engine Management
```python
# Create Redis connection pool
await storage.create_redis_engine()

# Close Redis connection
await storage.close()
```

#### Session Management
```python
# Create Redis session (recommended to use context manager)
async with storage.create_db_session() as conn:
    # Execute Redis operations here
    pass

# Create raw session (requires manual management)
conn = await storage.create_redis_session()
```

### 2. CRUD Operations

#### Add Data
```python
async with storage.create_db_session() as conn:
    # String operation
    command = RedisCommand(
        method="set",
        args=("key", "value"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, command)

    # Hash operation
    hash_command = RedisCommand(
        method="hset",
        args=("hash_key", "field", "value")
    )
    await storage.add(conn, hash_command)
```

#### Get Data
```python
async with storage.create_db_session() as conn:
    # Get string
    get_command = RedisCommand(method="get", args=("key",))
    result = await storage.get(conn, get_command)

    # Get hash
    hget_command = RedisCommand(method="hgetall", args=("hash_key",))
    hash_result = await storage.get(conn, hget_command)
```

#### Query Data
```python
async with storage.create_db_session() as conn:
    # Simple query
    condition = RedisCondition(limit=10)
    keys = await storage.query(conn, "user:*", condition)

    # Unlimited query
    all_condition = RedisCondition(limit=-1)
    all_keys = await storage.query(conn, "*", all_condition)
```

#### Delete Data
```python
async with storage.create_db_session() as conn:
    # Delete a single key
    await storage.delete(conn, "key")

    # Delete multiple keys (query first, query returns list[tuple[str, Any]])
    condition = RedisCondition(limit=-1)
    keys_to_delete = await storage.query(conn, "temp:*", condition)
    for key, _value in keys_to_delete:
        await storage.delete(conn, key)
```

#### Commit and Refresh
```python
async with storage.create_db_session() as conn:
    # Redis operations are atomic by default, commit is mainly for compatibility
    await storage.commit(conn)

    # Refresh data (re-fetch)
    command = RedisCommand(method="get", args=("key",))
    await storage.refresh(conn, command)
```

### 3. Advanced Features

#### Dynamic Command Execution
```python
# Dynamically invoke Redis commands via __getattr__ (auto-manages connection, no need to pass conn manually)
# Note: Methods already defined in RedisStorage (e.g., get, add, delete) will not trigger dynamic invocation
await storage.set("key", "value", ex=300)
info = await storage.info()

# Execute arbitrary commands using execute_command (requires manually passing conn)
async with storage.create_db_session() as conn:
    command = RedisCommand(method="ping")
    pong = await storage.execute_command(conn, command)
```

#### Pipeline Operations
```python
async with storage.create_db_session() as conn:
    # Batch operations (simulated pipeline)
    commands = [
        RedisCommand(method="set", args=("key1", "value1")),
        RedisCommand(method="set", args=("key2", "value2")),
        RedisCommand(method="set", args=("key3", "value3"))
    ]

    for command in commands:
        await storage.add(conn, command)

    await storage.commit(conn)
```

## Redis Connection URL

### Basic Connection Format
```python
# Basic connection
redis_url = "redis://localhost:6379/0"

# Connect with password
redis_url = "redis://:password@localhost:6379/0"

# Full format
redis_url = "redis://username:password@host:port/database"

# SSL connection
redis_url = "rediss://:password@host:6380/0"

# Unix Socket connection
redis_url = "unix:///path/to/redis.sock?db=0"
```

### Connection Parameter Examples
```python
# Development environment
dev_url = "redis://localhost:6379/0"

# Testing environment
test_url = "redis://:test_password@redis-test:6379/1"

# Production environment
prod_url = "redis://:prod_password@redis-cluster:6379/0"

# With connection pool parameters
storage = RedisStorage(
    redis_url=prod_url,
    is_async=True,
    max_connections=20,
    socket_timeout=10,
    socket_connect_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30
)
```

## Redis Data Type Operations in Detail

### 1. String Operations

#### Basic String Operations
```python
async with storage.create_db_session() as conn:
    # Set string
    set_command = RedisCommand(
        method="set",
        args=("user:1", "Alice"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, set_command)

    # Get string
    get_command = RedisCommand(method="get", args=("user:1",))
    result = await storage.get(conn, get_command)
    print(f"User: {result.decode('utf-8')}")

    # Set multiple key-value pairs (mset is not in the methods supported by add, use execute_command instead)
    mset_command = RedisCommand(
        method="mset",
        args=({"user:1": "Alice", "user:2": "Bob"},),
    )
    await storage.execute_command(conn, mset_command)

    # Get multiple values
    mget_command = RedisCommand(method="mget", args=(["user:1", "user:2"]))
    results = await storage.get(conn, mget_command)
```

#### JSON String Operations
```python
import json

async with storage.create_db_session() as conn:
    # Store JSON data
    user_data = {"name": "Alice", "age": 30, "email": "alice@example.com"}
    json_command = RedisCommand(
        method="set",
        args=("user:profile:1", json.dumps(user_data)),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=7200))
    )
    await storage.add(conn, json_command)

    # Get and parse JSON data
    get_command = RedisCommand(method="get", args=("user:profile:1",))
    result = await storage.get(conn, get_command)
    if result:
        user_profile = json.loads(result.decode('utf-8'))
        print(f"User profile: {user_profile}")
```

### 2. Hash Operations

```python
async with storage.create_db_session() as conn:
    # Set hash field
    hset_command = RedisCommand(
        method="hset",
        args=("user:1", "name", "Alice"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, hset_command)

    # Set multiple hash fields
    hmset_command = RedisCommand(
        method="hmset",
        args=("user:1", {"age": "30", "email": "alice@example.com"})
    )
    await storage.add(conn, hmset_command)

    # Get a single hash field
    hget_command = RedisCommand(method="hget", args=("user:1", "name"))
    name = await storage.get(conn, hget_command)

    # Get all hash fields
    hgetall_command = RedisCommand(method="hgetall", args=("user:1",))
    user_data = await storage.get(conn, hgetall_command)
    print(f"User data: {user_data}")

    # Get multiple hash fields
    hmget_command = RedisCommand(method="hmget", args=("user:1", ["name", "age"]))
    fields = await storage.get(conn, hmget_command)
```

### 3. List Operations

```python
async with storage.create_db_session() as conn:
    # Push element to the left of the list
    lpush_command = RedisCommand(
        method="lpush",
        args=("activities", "User logged in"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=1800))
    )
    await storage.add(conn, lpush_command)

    # Push element to the right of the list
    rpush_command = RedisCommand(
        method="rpush",
        args=("activities", "User updated profile")
    )
    await storage.add(conn, rpush_command)

    # Get list range
    lrange_command = RedisCommand(method="lrange", args=("activities", 0, -1))
    activities = await storage.execute_command(conn, lrange_command)
    print(f"Activities: {activities}")

    # Get list length
    llen_command = RedisCommand(method="llen", args=("activities",))
    length = await storage.execute_command(conn, llen_command)

    # Pop element
    lpop_command = RedisCommand(method="lpop", args=("activities",))
    popped = await storage.execute_command(conn, lpop_command)
```

### 4. Set Operations

```python
async with storage.create_db_session() as conn:
    # Add set members
    sadd_command = RedisCommand(
        method="sadd",
        args=("active_users", "alice", "bob", "charlie"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=900))
    )
    await storage.add(conn, sadd_command)

    # Get all set members
    smembers_command = RedisCommand(method="smembers", args=("active_users",))
    members = await storage.execute_command(conn, smembers_command)
    print(f"Active users: {members}")

    # Check if member exists
    sismember_command = RedisCommand(method="sismember", args=("active_users", "alice"))
    is_member = await storage.execute_command(conn, sismember_command)

    # Get set size
    scard_command = RedisCommand(method="scard", args=("active_users",))
    size = await storage.execute_command(conn, scard_command)

    # Set operations
    sinter_command = RedisCommand(method="sinter", args=(["set1", "set2"]))
    intersection = await storage.execute_command(conn, sinter_command)
```

### 5. Sorted Set Operations

```python
async with storage.create_db_session() as conn:
    # Add sorted set members
    zadd_command = RedisCommand(
        method="zadd",
        args=("leaderboard", {"alice": 100, "bob": 85, "charlie": 92}),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, zadd_command)

    # Get sorted set members (ascending by score)
    zrange_command = RedisCommand(
        method="zrange",
        args=("leaderboard", 0, -1),
        kwargs={"withscores": True}
    )
    ascending = await storage.execute_command(conn, zrange_command)

    # Get sorted set members (descending by score)
    zrevrange_command = RedisCommand(
        method="zrevrange",
        args=("leaderboard", 0, -1),
        kwargs={"withscores": True}
    )
    descending = await storage.execute_command(conn, zrevrange_command)
    print(f"Leaderboard: {descending}")

    # Get members within a score range
    zrangebyscore_command = RedisCommand(
        method="zrangebyscore",
        args=("leaderboard", 80, 100),
        kwargs={"withscores": True}
    )
    score_range = await storage.execute_command(conn, zrangebyscore_command)

    # Get member rank
    zrank_command = RedisCommand(method="zrank", args=("leaderboard", "alice"))
    rank = await storage.execute_command(conn, zrank_command)
```

### 6. Expiration Time Settings

```python
# Several ways to set expiration time
async with storage.create_db_session() as conn:
    # Method 1: Set expiration time in the command
    command_with_expire = RedisCommand(
        method="set",
        args=("temp_key", "temp_value"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))  # Expires after 1 hour
    )
    await storage.add(conn, command_with_expire)

    # Method 2: Use the EX parameter of the SET command
    set_ex_command = RedisCommand(
        method="set",
        args=("temp_key2", "temp_value2"),
        kwargs={"ex": 1800}  # Expires after 30 minutes
    )
    await storage.add(conn, set_ex_command)

    # Method 3: Set expiration time separately (expire is not in the methods supported by add, use execute_command instead)
    expire_command = RedisCommand(method="expire", args=("existing_key", 7200))
    await storage.execute_command(conn, expire_command)

    # Check remaining expiration time
    ttl_command = RedisCommand(method="ttl", args=("temp_key",))
    remaining_time = await storage.execute_command(conn, ttl_command)
    print(f"Remaining TTL: {remaining_time} seconds")
```

## Complete Usage Example

### Practical Application Example
```python
import asyncio
import json
from datetime import datetime, timedelta
from trpc_agent_sdk.storage import RedisStorage, RedisCommand, RedisExpire, RedisCondition
from trpc_agent_sdk.types import Ttl

class CacheService:
    """Cache service class example"""

    def __init__(self, redis_url: str):
        self.storage = RedisStorage(
            redis_url=redis_url,
            is_async=True,
            decode_responses=True,
            max_connections=20,
            socket_timeout=10,
            retry_on_timeout=True
        )

    async def initialize(self):
        """Initialize Redis connection"""
        await self.storage.create_redis_engine()

    async def cache_user_session(self, user_id: int, session_data: dict, expire_hours: int = 24) -> bool:
        """Cache user session"""
        async with self.storage.create_db_session() as conn:
            session_key = f"session:user:{user_id}"
            command = RedisCommand(
                method="set",
                args=(session_key, json.dumps(session_data)),
                expire=RedisExpire(ttl=Ttl(ttl_seconds=expire_hours * 3600))
            )
            await self.storage.add(conn, command)
            return True

    async def get_user_session(self, user_id: int) -> dict:
        """Get user session"""
        async with self.storage.create_db_session() as conn:
            session_key = f"session:user:{user_id}"
            command = RedisCommand(method="get", args=(session_key,))
            result = await self.storage.get(conn, command)

            if result:
                return json.loads(result)
            return None

    async def cache_user_profile(self, user_id: int, profile: dict) -> bool:
        """Cache user profile (using hash)"""
        async with self.storage.create_db_session() as conn:
            profile_key = f"profile:user:{user_id}"

            # Convert dictionary to hash fields
            for field, value in profile.items():
                command = RedisCommand(
                    method="hset",
                    args=(profile_key, field, str(value)),
                    expire=RedisExpire(ttl=Ttl(ttl_seconds=7200))  # 2 hours
                )
                await self.storage.add(conn, command)

            return True

    async def get_user_profile(self, user_id: int) -> dict:
        """Get user profile"""
        async with self.storage.create_db_session() as conn:
            profile_key = f"profile:user:{user_id}"
            command = RedisCommand(method="hgetall", args=(profile_key,))
            result = await self.storage.get(conn, command)

            if result:
                # Convert back to appropriate data types
                profile = {}
                for key, value in result.items():
                    if key in ['age', 'score']:
                        profile[key] = int(value)
                    elif key in ['rating']:
                        profile[key] = float(value)
                    else:
                        profile[key] = value
                return profile
            return {}

    async def add_user_activity(self, user_id: int, activity: str) -> bool:
        """Add user activity (using list)"""
        async with self.storage.create_db_session() as conn:
            activity_key = f"activities:user:{user_id}"
            timestamp = datetime.now().isoformat()
            activity_with_time = f"{timestamp}: {activity}"

            command = RedisCommand(
                method="lpush",
                args=(activity_key, activity_with_time),
                expire=RedisExpire(ttl=Ttl(ttl_seconds=86400))  # 1 day
            )
            await self.storage.add(conn, command)

            # Keep list length within 100
            trim_command = RedisCommand(method="ltrim", args=(activity_key, 0, 99))
            await self.storage.execute_command(conn, trim_command)

            return True

    async def get_user_activities(self, user_id: int, limit: int = 10) -> list:
        """Get user activities"""
        async with self.storage.create_db_session() as conn:
            activity_key = f"activities:user:{user_id}"
            command = RedisCommand(method="lrange", args=(activity_key, 0, limit - 1))
            result = await self.storage.execute_command(conn, command)

            return result if result else []

    async def add_to_leaderboard(self, user_id: int, score: float) -> bool:
        """Add to leaderboard (using sorted set)"""
        async with self.storage.create_db_session() as conn:
            leaderboard_key = "leaderboard:global"
            command = RedisCommand(
                method="zadd",
                args=(leaderboard_key, {f"user:{user_id}": score}),
                expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))  # 1 hour
            )
            await self.storage.add(conn, command)
            return True

    async def get_leaderboard(self, limit: int = 10) -> list:
        """Get leaderboard"""
        async with self.storage.create_db_session() as conn:
            leaderboard_key = "leaderboard:global"
            command = RedisCommand(
                method="zrevrange",
                args=(leaderboard_key, 0, limit - 1),
                kwargs={"withscores": True}
            )
            result = await self.storage.execute_command(conn, command)

            if result:
                leaderboard = [
                    {"user": member, "score": score}
                    for member, score in result
                ]
                return leaderboard
            return []

    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions"""
        async with self.storage.create_db_session() as conn:
            condition = RedisCondition(limit=-1)
            session_keys = await self.storage.query(conn, "session:*", condition)

            cleaned_count = 0
            for key, _value in session_keys:
                # Check if the key still exists (may have expired or been deleted); use execute_command for EXISTS—not get (get only allows methods whose names contain "get")
                exists = await self.storage.execute_command(
                    conn, RedisCommand(method="exists", args=(key,))
                )

                if not exists:
                    cleaned_count += 1

            return cleaned_count

    async def get_cache_stats(self) -> dict:
        """Get cache statistics"""
        async with self.storage.create_db_session() as conn:
            info_command = RedisCommand(method="info", args=("memory",))
            memory_info = await self.storage.execute_command(conn, info_command)

            dbsize_command = RedisCommand(method="dbsize")
            db_size = await self.storage.execute_command(conn, dbsize_command)

            return {
                "total_keys": db_size,
                "memory_info": memory_info,
                "timestamp": datetime.now().isoformat()
            }

    async def close(self):
        """Close Redis connection"""
        await self.storage.close()

# Usage example
async def main():
    service = CacheService("redis://localhost:6379/0")

    try:
        await service.initialize()

        # Cache user session
        session_data = {
            "user_id": 1,
            "username": "alice",
            "login_time": datetime.now().isoformat(),
            "permissions": ["read", "write"]
        }
        await service.cache_user_session(1, session_data)

        # Get user session
        session = await service.get_user_session(1)
        print(f"User session: {session}")

        # Cache user profile
        profile = {
            "name": "Alice",
            "age": 30,
            "email": "alice@example.com",
            "rating": 4.5
        }
        await service.cache_user_profile(1, profile)

        # Get user profile
        cached_profile = await service.get_user_profile(1)
        print(f"User profile: {cached_profile}")

        # Add user activity
        await service.add_user_activity(1, "Logged in")
        await service.add_user_activity(1, "Updated profile")

        # Get user activities
        activities = await service.get_user_activities(1)
        print(f"User activities: {activities}")

        # Add to leaderboard
        await service.add_to_leaderboard(1, 95.5)
        await service.add_to_leaderboard(2, 87.2)
        await service.add_to_leaderboard(3, 92.8)

        # Get leaderboard
        leaderboard = await service.get_leaderboard()
        print(f"Leaderboard: {leaderboard}")

        # Get cache statistics
        stats = await service.get_cache_stats()
        print(f"Cache stats: {stats}")

    finally:
        await service.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Error Handling and Best Practices

### 1. Exception Handling
```python
from redis.exceptions import ConnectionError, TimeoutError, RedisError

async def safe_redis_operation(storage, key: str, value: str):
    """Safe Redis operation example"""
    async with storage.create_db_session() as conn:
        try:
            command = RedisCommand(method="set", args=(key, value))
            await storage.add(conn, command)
            return True

        except ConnectionError as e:
            print(f"Redis connection error: {e}")
            return False

        except TimeoutError as e:
            print(f"Redis operation timeout: {e}")
            return False

        except RedisError as e:
            print(f"Redis error: {e}")
            return False

        except Exception as e:
            print(f"Unknown error: {e}")
            return False
```

### 2. Connection Management
```python
class RedisManager:
    """Redis connection manager"""

    def __init__(self, redis_url: str):
        self.storage = None
        self.redis_url = redis_url

    async def __aenter__(self):
        self.storage = RedisStorage(
            redis_url=self.redis_url,
            is_async=True,
            max_connections=10,
            socket_timeout=5,
            retry_on_timeout=True
        )
        await self.storage.create_redis_engine()
        return self.storage

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.storage:
            await self.storage.close()

# Usage
async def example_with_manager():
    async with RedisManager("redis://localhost:6379/0") as storage:
        async with storage.create_db_session() as conn:
            # Execute Redis operations
            pass
```

### 3. Performance Optimization Recommendations

#### Connection Pool Configuration
```python
storage = RedisStorage(
    redis_url=redis_url,
    is_async=True,
    max_connections=20,        # Connection pool size
    socket_timeout=10,         # Socket timeout
    socket_connect_timeout=5,  # Connection timeout
    retry_on_timeout=True,     # Retry on timeout
    health_check_interval=30,  # Health check interval
    decode_responses=True      # Auto-decode responses
)
```

#### Batch Operations
```python
async def batch_set_keys(storage, key_value_pairs: dict):
    """Batch set key-value pairs"""
    async with storage.create_db_session() as conn:
        try:
            # Use MSET for batch setting (mset is not in the methods supported by add, use execute_command instead)
            mset_command = RedisCommand(method="mset", args=(key_value_pairs,))
            await storage.execute_command(conn, mset_command)

            print(f"Successfully set {len(key_value_pairs)} keys")

        except Exception as e:
            print(f"Batch operation failed: {e}")
```

#### Memory Optimization
```python
async def optimize_memory_usage(storage):
    """Memory usage optimization"""
    async with storage.create_db_session() as conn:
        # Set appropriate expiration times
        expire_policies = {
            "session:*": 86400,    # Session data: 1 day
            "cache:*": 3600,       # Cache data: 1 hour
            "temp:*": 300          # Temporary data: 5 minutes
        }

        for pattern, expire_time in expire_policies.items():
            condition = RedisCondition(limit=-1)
            keys = await storage.query(conn, pattern, condition)

            for key, _value in keys:
                expire_command = RedisCommand(method="expire", args=(key, expire_time))
                await storage.execute_command(conn, expire_command)
```

### 4. Monitoring and Debugging

#### Enable Verbose Logging
```python
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
redis_logger = logging.getLogger('redis')
redis_logger.setLevel(logging.DEBUG)

# Create storage instance with logging
storage = RedisStorage(
    redis_url=redis_url,
    is_async=True,
    decode_responses=True
)
```

#### Performance Monitoring
```python
async def monitor_redis_performance(storage):
    """Monitor Redis performance"""
    async with storage.create_db_session() as conn:
        # Get Redis info
        info_command = RedisCommand(method="info")
        info = await storage.execute_command(conn, info_command)

        # Parse key metrics
        if info:
            lines = info.split('\n')
            metrics = {}
            for line in lines:
                if ':' in line and not line.startswith('#'):
                    key, value = line.strip().split(':', 1)
                    metrics[key] = value

            # Output key metrics
            print(f"Used Memory: {metrics.get('used_memory_human', 'N/A')}")
            print(f"Connected Clients: {metrics.get('connected_clients', 'N/A')}")
            print(f"Total Commands: {metrics.get('total_commands_processed', 'N/A')}")
            print(f"Keyspace Hits: {metrics.get('keyspace_hits', 'N/A')}")
            print(f"Keyspace Misses: {metrics.get('keyspace_misses', 'N/A')}")
```

## Troubleshooting

### Common Errors and Solutions

1. **Connection Error**
   ```python
   # Error: ConnectionError: Error connecting to Redis
   # Solution: Check Redis service status and connection parameters

   # Test connection
   try:
       await storage.create_redis_engine()
       print("Redis connection successful")
   except Exception as e:
       print(f"Connection failed: {e}")
   ```

2. **Authentication Error**
   ```python
   # Error: AuthenticationError: Authentication required
   # Solution: Check password configuration

   # Correct password format
   redis_url = "redis://:your_password@localhost:6379/0"
   ```

3. **Timeout Error**
   ```python
   # Error: TimeoutError: Timeout reading from socket
   # Solution: Adjust timeout settings

   storage = RedisStorage(
       redis_url=redis_url,
       socket_timeout=30,           # Increase timeout
       socket_connect_timeout=10,   # Increase connection timeout
       retry_on_timeout=True        # Enable timeout retry
   )
   ```

4. **Out of Memory Error**
   ```python
   # Error: OOM command not allowed when used memory > 'maxmemory'
   # Solution: Clean up expired data or increase memory

   async def cleanup_expired_data(storage):
       async with storage.create_db_session() as conn:
           # Add TTL to keys that have none (not "delete expired keys"—expired keys are usually already removed by Redis)
           condition = RedisCondition(limit=-1)
           all_keys = await storage.query(conn, "*", condition)

           updated_count = 0
           for key, _value in all_keys:
               ttl_command = RedisCommand(method="ttl", args=(key,))
               ttl = await storage.execute_command(conn, ttl_command)

               if ttl == -1:  # Key exists but has no expiration
                   expire_command = RedisCommand(method="expire", args=(key, 3600))
                   await storage.execute_command(conn, expire_command)
                   updated_count += 1

           print(f"Set expiration on {updated_count} keys that had no TTL")
   ```

### Performance Issue Diagnosis

#### Connection Pool Monitoring
```python
async def diagnose_connection_pool(storage):
    """Diagnose connection pool status"""
    async with storage.create_db_session() as conn:
        info_command = RedisCommand(method="info", args=("clients",))
        client_info = await storage.execute_command(conn, info_command)

        if client_info:
            print("Client Information:")
            for line in client_info.split('\n'):
                if line.startswith('connected_clients'):
                    print(f"  {line}")
                elif line.startswith('client_recent_max_input_buffer'):
                    print(f"  {line}")
                elif line.startswith('client_recent_max_output_buffer'):
                    print(f"  {line}")
```

#### Memory Usage Analysis
```python
async def analyze_memory_usage(storage):
    """Analyze memory usage"""
    async with storage.create_db_session() as conn:
        info_command = RedisCommand(method="info", args=("memory",))
        memory_info = await storage.execute_command(conn, info_command)

        if memory_info:
            print("Memory Usage Analysis:")
            important_metrics = [
                'used_memory_human',
                'used_memory_peak_human',
                'used_memory_rss_human',
                'mem_fragmentation_ratio',
                'maxmemory_human'
            ]

            for line in memory_info.split('\n'):
                for metric in important_metrics:
                    if line.startswith(metric):
                        print(f"  {line}")
```

### Debug Mode and Logging

#### Verbose Logging Configuration
```python
import logging

# Configure Redis-related logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Set specific log levels
redis_logger = logging.getLogger('redis')
redis_logger.setLevel(logging.INFO)  # Show important info only

# Set connection logging
connection_logger = logging.getLogger('redis.connection')
connection_logger.setLevel(logging.DEBUG)  # Show connection details
```

#### Command Execution Tracing
```python
import time

class DebugRedisStorage(RedisStorage):
    """Redis storage with debugging capabilities"""

    async def add(self, conn, command):
        print(f"Executing command: {command.method} with args: {command.args}")
        start_time = time.time()

        try:
            result = await super().add(conn, command)
            execution_time = time.time() - start_time
            print(f"Command completed in {execution_time:.3f}s")
            return result
        except Exception as e:
            execution_time = time.time() - start_time
            print(f"Command failed after {execution_time:.3f}s: {e}")
            raise
```

## Running Examples

### Basic Run
```bash
# 1. Ensure Redis service is running
redis-server

# 2. Install dependencies
pip install redis

# 3. Run the complete example
python examples/storage/redis_example.py

# 4. Run specific operation examples
python -c "
import asyncio
from examples.storage.redis_example import RedisExampleManager

async def main():
    manager = RedisExampleManager()
    await manager.setup_redis()
    await manager.string_operations_example()
    await manager.close_connection()

asyncio.run(main())
"
```

### Run with Environment Configuration
```bash
# Set environment variables
export REDIS_HOST=localhost
export REDIS_PORT=6379
export REDIS_DB=0
export REDIS_PASSWORD=your_password
export REDIS_MAX_CONNECTIONS=20

# Run example
python examples/storage/redis_example.py
```

### Run in Docker Environment
```bash
# Start Redis container
docker run -d --name redis-test -p 6379:6379 redis:latest

# Run example (connect to container)
REDIS_HOST=localhost python examples/storage/redis_example.py

# Clean up container
docker stop redis-test && docker rm redis-test
```

### Run in Cluster Environment
```bash
# Connect to Redis cluster
export REDIS_URL="redis://redis-cluster:6379/0"
python examples/storage/redis_example.py

# Or specify in code
python -c "
import asyncio
from examples.storage.redis_example import RedisExampleManager

async def main():
    manager = RedisExampleManager('redis://redis-cluster:6379/0')
    await manager.setup_redis()
    # ... other operations
    await manager.close_connection()

asyncio.run(main())
"
```

### Run Performance Tests
```bash
# Run performance test
python -c "
import asyncio
import time
from examples.storage.redis_example import RedisExampleManager

async def performance_test():
    manager = RedisExampleManager()
    await manager.setup_redis()

    # Test bulk writes
    start_time = time.time()
    for i in range(1000):
        await manager.string_operations_example()

    end_time = time.time()
    print(f'1000 operations completed in {end_time - start_time:.2f} seconds')

    await manager.close_connection()

asyncio.run(performance_test())
"
```
