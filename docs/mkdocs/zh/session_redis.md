# RedisStorage 使用指南

本文档详细介绍如何使用 `RedisStorage` 类进行 Redis 数据库操作，包括字符串、哈希、列表、集合、有序集合等数据类型。

## 概述

`RedisStorage` 是一个基于 redis-py 的异步/同步 Redis 存储实现，提供了统一的接口来处理各种 Redis 数据操作。

## 核心组件

### 1. RedisStorage 类
主要的存储类，提供 Redis 连接和操作接口。

### 2. 辅助类
- `RedisCommand`: 用于构建 Redis 命令，例如：SET, GET, HSET 等
- `RedisExpire`: 用于 Redis 过期时间的操作命令，通过 `Ttl` 对象设置过期策略
- `RedisCondition`: 用于查询的条件过滤类
- `Ttl`: 用于配置 TTL（Time-To-Live）过期时间，包含 `ttl_seconds`（过期秒数）、`enable`（是否启用）等字段

## 前置条件

### 1. 安装必需的依赖

```bash
# 核心依赖
pip install redis

# 异步支持（可选，推荐）
pip install redis[hiredis]
```

### 2. Redis 服务器设置

#### 使用 Docker 启动 Redis
```bash
# 基本启动
docker run -d -p 6379:6379 redis:latest

# 带密码启动
docker run -d -p 6379:6379 redis:latest redis-server --requirepass mypassword

# 持久化数据
docker run -d -p 6379:6379 -v redis-data:/data redis:latest redis-server --appendonly yes
```

#### 本地安装 Redis
```bash
# Ubuntu/Debian
sudo apt-get install redis-server

# CentOS/RHEL
sudo yum install redis

# macOS
brew install redis

# 启动服务
redis-server
```

#### Redis 配置优化
```bash
# 编辑 redis.conf
# 设置最大内存
maxmemory 2gb
maxmemory-policy allkeys-lru

# 启用持久化
save 900 1
save 300 10
save 60 10000

# 网络配置
bind 127.0.0.1
port 6379
timeout 300
```
## 基本使用方法

### 1. 初始化 RedisStorage

```python
from trpc_agent_sdk.storage import RedisStorage

# 异步模式（推荐）
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

# 同步模式
storage = RedisStorage(
    redis_url="redis://localhost:6379/0",
    is_async=False,
    decode_responses=True
)

# 带密码连接
storage = RedisStorage(
    redis_url="redis://:password@localhost:6379/0",
    is_async=True
)
```

### 2. 定义 Redis 命令

```python
from trpc_agent_sdk.storage import RedisCommand, RedisExpire, RedisCondition
from trpc_agent_sdk.types import Ttl

# 字符串操作命令
set_command = RedisCommand(
    method="set",
    args=("user:1", "{'name': 'Alice', 'age': 30}"),
    expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))  # 1小时后过期
)

get_command = RedisCommand(
    method="get",
    args=("user:1",)
)

# 哈希操作命令
hset_command = RedisCommand(
    method="hset",
    args=("user_profile:1", "name", "Alice"),
    expire=RedisExpire(ttl=Ttl(ttl_seconds=7200))  # 2小时后过期
)

hgetall_command = RedisCommand(
    method="hgetall",
    args=("user_profile:1",)
)
```

### 3. 基本操作示例

```python
import asyncio
import json
from trpc_agent_sdk.storage import RedisStorage, RedisCommand, RedisExpire, RedisCondition
from trpc_agent_sdk.types import Ttl

async def basic_example():
    # 初始化存储
    storage = RedisStorage(
        redis_url="redis://localhost:6379/0",
        is_async=True
    )

    try:
        # 创建 Redis 连接池
        await storage.create_redis_engine()

        # 使用 Redis 会话
        async with storage.create_db_session() as conn:
            # 设置字符串值
            user_data = {"name": "Alice", "age": 30, "email": "alice@example.com"}
            set_command = RedisCommand(
                method="set",
                args=("user:1", json.dumps(user_data)),
                expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
            )
            await storage.add(conn, set_command)

            print("User data saved successfully")

            # 获取字符串值
            get_command = RedisCommand(method="get", args=("user:1",))
            result = await storage.get(conn, get_command)

            if result:
                retrieved_data = json.loads(result.decode('utf-8'))
                print(f"Retrieved user: {retrieved_data}")

            # 查询匹配模式的键
            condition = RedisCondition(limit=10)
            keys = await storage.query(conn, "user:*", condition)
            print(f"Found {len(keys)} user keys")

            # 删除键
            await storage.delete(conn, "user:1")
            print("User data deleted")

    finally:
        await storage.close()

# 运行示例
asyncio.run(basic_example())
```

### 4. 配置管理

```python
import os
from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class RedisConfig:
    """Redis 配置类"""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None

    # 连接池设置
    max_connections: int = 10
    socket_timeout: int = 5
    socket_connect_timeout: int = 5
    retry_on_timeout: bool = True
    health_check_interval: int = 30

    # Redis 设置
    decode_responses: bool = True

    def get_redis_url(self) -> str:
        """获取 Redis 连接 URL"""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"

    def get_connection_kwargs(self) -> Dict[str, Any]:
        """获取连接参数"""
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
        """从环境变量创建配置"""
        return cls(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            password=os.getenv("REDIS_PASSWORD"),
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "10")),
            socket_timeout=int(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
        )

# 使用配置
config = RedisConfig.from_env()
storage = RedisStorage(
    redis_url=config.get_redis_url(),
    is_async=True,
    **config.get_connection_kwargs()
)
```

### 5. 环境变量配置

设置环境变量来配置 Redis 连接：

```bash
# 基本连接配置
export REDIS_HOST=localhost
export REDIS_PORT=6379
export REDIS_DB=0
export REDIS_PASSWORD=your_password

# 连接池配置
export REDIS_MAX_CONNECTIONS=20
export REDIS_SOCKET_TIMEOUT=10
export REDIS_CONNECT_TIMEOUT=5

# 其他配置
export REDIS_DECODE_RESPONSES=true
export REDIS_RETRY_ON_TIMEOUT=true
```

## RedisStorage 接口详解

### 1. 核心接口

#### Redis 引擎管理
```python
# 创建 Redis 连接池
await storage.create_redis_engine()

# 关闭 Redis 连接
await storage.close()
```

#### 会话管理
```python
# 创建 Redis 会话（推荐使用上下文管理器）
async with storage.create_db_session() as conn:
    # 在这里执行 Redis 操作
    pass

# 创建原始会话（需要手动管理）
conn = await storage.create_redis_session()
```

### 2. CRUD 操作

#### 添加数据
```python
async with storage.create_db_session() as conn:
    # 字符串操作
    command = RedisCommand(
        method="set",
        args=("key", "value"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, command)

    # 哈希操作
    hash_command = RedisCommand(
        method="hset",
        args=("hash_key", "field", "value")
    )
    await storage.add(conn, hash_command)
```

#### 获取数据
```python
async with storage.create_db_session() as conn:
    # 获取字符串
    get_command = RedisCommand(method="get", args=("key",))
    result = await storage.get(conn, get_command)

    # 获取哈希
    hget_command = RedisCommand(method="hgetall", args=("hash_key",))
    hash_result = await storage.get(conn, hget_command)
```

#### 查询数据
```python
async with storage.create_db_session() as conn:
    # 简单查询
    condition = RedisCondition(limit=10)
    keys = await storage.query(conn, "user:*", condition)

    # 无限制查询
    all_condition = RedisCondition(limit=-1)
    all_keys = await storage.query(conn, "*", all_condition)
```

#### 删除数据
```python
async with storage.create_db_session() as conn:
    # 删除单个键
    await storage.delete(conn, "key")

    # 删除多个键（需要先查询，query 返回 list[tuple[str, Any]]）
    condition = RedisCondition(limit=-1)
    keys_to_delete = await storage.query(conn, "temp:*", condition)
    for key, _value in keys_to_delete:
        await storage.delete(conn, key)
```

#### 提交和刷新
```python
async with storage.create_db_session() as conn:
    # Redis 操作默认是原子的，commit 主要用于兼容性
    await storage.commit(conn)

    # 刷新数据（重新获取）
    command = RedisCommand(method="get", args=("key",))
    await storage.refresh(conn, command)
```

### 3. 高级功能

#### 动态命令执行
```python
# 通过 __getattr__ 动态调用 Redis 命令（自动管理连接，无需手动传入 conn）
# 注意：已在 RedisStorage 中定义的方法（如 get、add、delete）不会触发动态调用
await storage.set("key", "value", ex=300)
info = await storage.info()

# 使用 execute_command 执行任意命令（需手动传入 conn）
async with storage.create_db_session() as conn:
    command = RedisCommand(method="ping")
    pong = await storage.execute_command(conn, command)
```

#### 管道操作
```python
async with storage.create_db_session() as conn:
    # 批量操作（模拟管道）
    commands = [
        RedisCommand(method="set", args=("key1", "value1")),
        RedisCommand(method="set", args=("key2", "value2")),
        RedisCommand(method="set", args=("key3", "value3"))
    ]

    for command in commands:
        await storage.add(conn, command)

    await storage.commit(conn)
```

## Redis 连接 URL

### 基本连接格式
```python
# 基本连接
redis_url = "redis://localhost:6379/0"

# 带密码连接
redis_url = "redis://:password@localhost:6379/0"

# 完整格式
redis_url = "redis://username:password@host:port/database"

# SSL 连接
redis_url = "rediss://:password@host:6380/0"

# Unix Socket 连接
redis_url = "unix:///path/to/redis.sock?db=0"
```

### 连接参数示例
```python
# 开发环境
dev_url = "redis://localhost:6379/0"

# 测试环境
test_url = "redis://:test_password@redis-test:6379/1"

# 生产环境
prod_url = "redis://:prod_password@redis-cluster:6379/0"

# 带连接池参数
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

## Redis 数据类型操作详解

### 1. 字符串（String）操作

#### 基本字符串操作
```python
async with storage.create_db_session() as conn:
    # 设置字符串
    set_command = RedisCommand(
        method="set",
        args=("user:1", "Alice"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, set_command)

    # 获取字符串
    get_command = RedisCommand(method="get", args=("user:1",))
    result = await storage.get(conn, get_command)
    print(f"User: {result.decode('utf-8')}")

    # 设置多个键值对（mset 不在 add 支持的方法中，需使用 execute_command）
    mset_command = RedisCommand(
        method="mset",
        args=({"user:1": "Alice", "user:2": "Bob"},),
    )
    await storage.execute_command(conn, mset_command)

    # 获取多个值
    mget_command = RedisCommand(method="mget", args=(["user:1", "user:2"]))
    results = await storage.get(conn, mget_command)
```

#### JSON 字符串操作
```python
import json

async with storage.create_db_session() as conn:
    # 存储 JSON 数据
    user_data = {"name": "Alice", "age": 30, "email": "alice@example.com"}
    json_command = RedisCommand(
        method="set",
        args=("user:profile:1", json.dumps(user_data)),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=7200))
    )
    await storage.add(conn, json_command)

    # 获取并解析 JSON 数据
    get_command = RedisCommand(method="get", args=("user:profile:1",))
    result = await storage.get(conn, get_command)
    if result:
        user_profile = json.loads(result.decode('utf-8'))
        print(f"User profile: {user_profile}")
```

### 2. 哈希（Hash）操作

```python
async with storage.create_db_session() as conn:
    # 设置哈希字段
    hset_command = RedisCommand(
        method="hset",
        args=("user:1", "name", "Alice"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, hset_command)

    # 设置多个哈希字段
    hmset_command = RedisCommand(
        method="hmset",
        args=("user:1", {"age": "30", "email": "alice@example.com"})
    )
    await storage.add(conn, hmset_command)

    # 获取单个哈希字段
    hget_command = RedisCommand(method="hget", args=("user:1", "name"))
    name = await storage.get(conn, hget_command)

    # 获取所有哈希字段
    hgetall_command = RedisCommand(method="hgetall", args=("user:1",))
    user_data = await storage.get(conn, hgetall_command)
    print(f"User data: {user_data}")

    # 获取多个哈希字段
    hmget_command = RedisCommand(method="hmget", args=("user:1", ["name", "age"]))
    fields = await storage.get(conn, hmget_command)
```

### 3. 列表（List）操作

```python
async with storage.create_db_session() as conn:
    # 向列表左侧推入元素
    lpush_command = RedisCommand(
        method="lpush",
        args=("activities", "User logged in"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=1800))
    )
    await storage.add(conn, lpush_command)

    # 向列表右侧推入元素
    rpush_command = RedisCommand(
        method="rpush",
        args=("activities", "User updated profile")
    )
    await storage.add(conn, rpush_command)

    # 获取列表范围
    lrange_command = RedisCommand(method="lrange", args=("activities", 0, -1))
    activities = await storage.execute_command(conn, lrange_command)
    print(f"Activities: {activities}")

    # 获取列表长度
    llen_command = RedisCommand(method="llen", args=("activities",))
    length = await storage.execute_command(conn, llen_command)

    # 弹出元素
    lpop_command = RedisCommand(method="lpop", args=("activities",))
    popped = await storage.execute_command(conn, lpop_command)
```

### 4. 集合（Set）操作

```python
async with storage.create_db_session() as conn:
    # 添加集合成员
    sadd_command = RedisCommand(
        method="sadd",
        args=("active_users", "alice", "bob", "charlie"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=900))
    )
    await storage.add(conn, sadd_command)

    # 获取所有集合成员
    smembers_command = RedisCommand(method="smembers", args=("active_users",))
    members = await storage.execute_command(conn, smembers_command)
    print(f"Active users: {members}")

    # 检查成员是否存在
    sismember_command = RedisCommand(method="sismember", args=("active_users", "alice"))
    is_member = await storage.execute_command(conn, sismember_command)

    # 获取集合大小
    scard_command = RedisCommand(method="scard", args=("active_users",))
    size = await storage.execute_command(conn, scard_command)

    # 集合运算
    sinter_command = RedisCommand(method="sinter", args=(["set1", "set2"]))
    intersection = await storage.execute_command(conn, sinter_command)
```

### 5. 有序集合（Sorted Set）操作

```python
async with storage.create_db_session() as conn:
    # 添加有序集合成员
    zadd_command = RedisCommand(
        method="zadd",
        args=("leaderboard", {"alice": 100, "bob": 85, "charlie": 92}),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))
    )
    await storage.add(conn, zadd_command)

    # 获取有序集合成员（按分数升序）
    zrange_command = RedisCommand(
        method="zrange",
        args=("leaderboard", 0, -1),
        kwargs={"withscores": True}
    )
    ascending = await storage.execute_command(conn, zrange_command)

    # 获取有序集合成员（按分数降序）
    zrevrange_command = RedisCommand(
        method="zrevrange",
        args=("leaderboard", 0, -1),
        kwargs={"withscores": True}
    )
    descending = await storage.execute_command(conn, zrevrange_command)
    print(f"Leaderboard: {descending}")

    # 获取分数范围内的成员
    zrangebyscore_command = RedisCommand(
        method="zrangebyscore",
        args=("leaderboard", 80, 100),
        kwargs={"withscores": True}
    )
    score_range = await storage.execute_command(conn, zrangebyscore_command)

    # 获取成员排名
    zrank_command = RedisCommand(method="zrank", args=("leaderboard", "alice"))
    rank = await storage.execute_command(conn, zrank_command)
```

### 6. 过期时间设置

```python
# 设置过期时间的几种方式
async with storage.create_db_session() as conn:
    # 方式1：在命令中设置过期时间
    command_with_expire = RedisCommand(
        method="set",
        args=("temp_key", "temp_value"),
        expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))  # 1小时后过期
    )
    await storage.add(conn, command_with_expire)

    # 方式2：使用 SET 命令的 EX 参数
    set_ex_command = RedisCommand(
        method="set",
        args=("temp_key2", "temp_value2"),
        kwargs={"ex": 1800}  # 30分钟后过期
    )
    await storage.add(conn, set_ex_command)

    # 方式3：单独设置过期时间（expire 不在 add 支持的方法中，需使用 execute_command）
    expire_command = RedisCommand(method="expire", args=("existing_key", 7200))
    await storage.execute_command(conn, expire_command)

    # 检查剩余过期时间
    ttl_command = RedisCommand(method="ttl", args=("temp_key",))
    remaining_time = await storage.execute_command(conn, ttl_command)
    print(f"Remaining TTL: {remaining_time} seconds")
```

## 完整使用示例

### 实际应用示例
```python
import asyncio
import json
from datetime import datetime, timedelta
from trpc_agent_sdk.storage import RedisStorage, RedisCommand, RedisExpire, RedisCondition
from trpc_agent_sdk.types import Ttl

class CacheService:
    """缓存服务类示例"""

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
        """初始化 Redis 连接"""
        await self.storage.create_redis_engine()

    async def cache_user_session(self, user_id: int, session_data: dict, expire_hours: int = 24) -> bool:
        """缓存用户会话"""
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
        """获取用户会话"""
        async with self.storage.create_db_session() as conn:
            session_key = f"session:user:{user_id}"
            command = RedisCommand(method="get", args=(session_key,))
            result = await self.storage.get(conn, command)

            if result:
                return json.loads(result)
            return None

    async def cache_user_profile(self, user_id: int, profile: dict) -> bool:
        """缓存用户资料（使用哈希）"""
        async with self.storage.create_db_session() as conn:
            profile_key = f"profile:user:{user_id}"

            # 将字典转换为哈希字段
            for field, value in profile.items():
                command = RedisCommand(
                    method="hset",
                    args=(profile_key, field, str(value)),
                    expire=RedisExpire(ttl=Ttl(ttl_seconds=7200))  # 2小时
                )
                await self.storage.add(conn, command)

            return True

    async def get_user_profile(self, user_id: int) -> dict:
        """获取用户资料"""
        async with self.storage.create_db_session() as conn:
            profile_key = f"profile:user:{user_id}"
            command = RedisCommand(method="hgetall", args=(profile_key,))
            result = await self.storage.get(conn, command)

            if result:
                # 转换回适当的数据类型
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
        """添加用户活动（使用列表）"""
        async with self.storage.create_db_session() as conn:
            activity_key = f"activities:user:{user_id}"
            timestamp = datetime.now().isoformat()
            activity_with_time = f"{timestamp}: {activity}"

            command = RedisCommand(
                method="lpush",
                args=(activity_key, activity_with_time),
                expire=RedisExpire(ttl=Ttl(ttl_seconds=86400))  # 1天
            )
            await self.storage.add(conn, command)

            # 保持列表长度不超过100
            trim_command = RedisCommand(method="ltrim", args=(activity_key, 0, 99))
            await self.storage.execute_command(conn, trim_command)

            return True

    async def get_user_activities(self, user_id: int, limit: int = 10) -> list:
        """获取用户活动"""
        async with self.storage.create_db_session() as conn:
            activity_key = f"activities:user:{user_id}"
            command = RedisCommand(method="lrange", args=(activity_key, 0, limit - 1))
            result = await self.storage.execute_command(conn, command)

            return result if result else []

    async def add_to_leaderboard(self, user_id: int, score: float) -> bool:
        """添加到排行榜（使用有序集合）"""
        async with self.storage.create_db_session() as conn:
            leaderboard_key = "leaderboard:global"
            command = RedisCommand(
                method="zadd",
                args=(leaderboard_key, {f"user:{user_id}": score}),
                expire=RedisExpire(ttl=Ttl(ttl_seconds=3600))  # 1小时
            )
            await self.storage.add(conn, command)
            return True

    async def get_leaderboard(self, limit: int = 10) -> list:
        """获取排行榜"""
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
        """清理过期会话"""
        async with self.storage.create_db_session() as conn:
            condition = RedisCondition(limit=-1)
            session_keys = await self.storage.query(conn, "session:*", condition)

            cleaned_count = 0
            for key, _value in session_keys:
                # 检查键是否仍存在（可能已过期或被删除）；exists 需用 execute_command，不能走 get（get 仅允许方法名含 get）
                exists = await self.storage.execute_command(
                    conn, RedisCommand(method="exists", args=(key,))
                )

                if not exists:
                    cleaned_count += 1

            return cleaned_count

    async def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
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
        """关闭 Redis 连接"""
        await self.storage.close()

# 使用示例
async def main():
    service = CacheService("redis://localhost:6379/0")

    try:
        await service.initialize()

        # 缓存用户会话
        session_data = {
            "user_id": 1,
            "username": "alice",
            "login_time": datetime.now().isoformat(),
            "permissions": ["read", "write"]
        }
        await service.cache_user_session(1, session_data)

        # 获取用户会话
        session = await service.get_user_session(1)
        print(f"User session: {session}")

        # 缓存用户资料
        profile = {
            "name": "Alice",
            "age": 30,
            "email": "alice@example.com",
            "rating": 4.5
        }
        await service.cache_user_profile(1, profile)

        # 获取用户资料
        cached_profile = await service.get_user_profile(1)
        print(f"User profile: {cached_profile}")

        # 添加用户活动
        await service.add_user_activity(1, "Logged in")
        await service.add_user_activity(1, "Updated profile")

        # 获取用户活动
        activities = await service.get_user_activities(1)
        print(f"User activities: {activities}")

        # 添加到排行榜
        await service.add_to_leaderboard(1, 95.5)
        await service.add_to_leaderboard(2, 87.2)
        await service.add_to_leaderboard(3, 92.8)

        # 获取排行榜
        leaderboard = await service.get_leaderboard()
        print(f"Leaderboard: {leaderboard}")

        # 获取缓存统计
        stats = await service.get_cache_stats()
        print(f"Cache stats: {stats}")

    finally:
        await service.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## 错误处理和最佳实践

### 1. 异常处理
```python
from redis.exceptions import ConnectionError, TimeoutError, RedisError

async def safe_redis_operation(storage, key: str, value: str):
    """安全的 Redis 操作示例"""
    async with storage.create_db_session() as conn:
        try:
            command = RedisCommand(method="set", args=(key, value))
            await storage.add(conn, command)
            return True

        except ConnectionError as e:
            print(f"Redis 连接错误: {e}")
            return False

        except TimeoutError as e:
            print(f"Redis 操作超时: {e}")
            return False

        except RedisError as e:
            print(f"Redis 错误: {e}")
            return False

        except Exception as e:
            print(f"未知错误: {e}")
            return False
```

### 2. 连接管理
```python
class RedisManager:
    """Redis 连接管理器"""

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

# 使用方式
async def example_with_manager():
    async with RedisManager("redis://localhost:6379/0") as storage:
        async with storage.create_db_session() as conn:
            # 执行 Redis 操作
            pass
```

### 3. 性能优化建议

#### 连接池配置
```python
storage = RedisStorage(
    redis_url=redis_url,
    is_async=True,
    max_connections=20,        # 连接池大小
    socket_timeout=10,         # Socket 超时时间
    socket_connect_timeout=5,  # 连接超时时间
    retry_on_timeout=True,     # 超时重试
    health_check_interval=30,  # 健康检查间隔
    decode_responses=True      # 自动解码响应
)
```

#### 批量操作
```python
async def batch_set_keys(storage, key_value_pairs: dict):
    """批量设置键值对"""
    async with storage.create_db_session() as conn:
        try:
            # 使用 MSET 进行批量设置（mset 不在 add 支持的方法中，需使用 execute_command）
            mset_command = RedisCommand(method="mset", args=(key_value_pairs,))
            await storage.execute_command(conn, mset_command)

            print(f"Successfully set {len(key_value_pairs)} keys")

        except Exception as e:
            print(f"Batch operation failed: {e}")
```

#### 内存优化
```python
async def optimize_memory_usage(storage):
    """内存使用优化"""
    async with storage.create_db_session() as conn:
        # 设置合适的过期时间
        expire_policies = {
            "session:*": 86400,    # 会话数据1天
            "cache:*": 3600,       # 缓存数据1小时
            "temp:*": 300          # 临时数据5分钟
        }

        for pattern, expire_time in expire_policies.items():
            condition = RedisCondition(limit=-1)
            keys = await storage.query(conn, pattern, condition)

            for key, _value in keys:
                expire_command = RedisCommand(method="expire", args=(key, expire_time))
                await storage.execute_command(conn, expire_command)
```

### 4. 监控和调试

#### 启用详细日志
```python
import logging

# 配置日志
logging.basicConfig(level=logging.DEBUG)
redis_logger = logging.getLogger('redis')
redis_logger.setLevel(logging.DEBUG)

# 创建带日志的存储实例
storage = RedisStorage(
    redis_url=redis_url,
    is_async=True,
    decode_responses=True
)
```

#### 性能监控
```python
async def monitor_redis_performance(storage):
    """监控 Redis 性能"""
    async with storage.create_db_session() as conn:
        # 获取 Redis 信息
        info_command = RedisCommand(method="info")
        info = await storage.execute_command(conn, info_command)

        # 解析关键指标
        if info:
            lines = info.split('\n')
            metrics = {}
            for line in lines:
                if ':' in line and not line.startswith('#'):
                    key, value = line.strip().split(':', 1)
                    metrics[key] = value

            # 输出关键指标
            print(f"Used Memory: {metrics.get('used_memory_human', 'N/A')}")
            print(f"Connected Clients: {metrics.get('connected_clients', 'N/A')}")
            print(f"Total Commands: {metrics.get('total_commands_processed', 'N/A')}")
            print(f"Keyspace Hits: {metrics.get('keyspace_hits', 'N/A')}")
            print(f"Keyspace Misses: {metrics.get('keyspace_misses', 'N/A')}")
```

## 故障排除

### 常见错误及解决方案

1. **连接错误**
   ```python
   # 错误: ConnectionError: Error connecting to Redis
   # 解决: 检查 Redis 服务状态和连接参数

   # 测试连接
   try:
       await storage.create_redis_engine()
       print("Redis 连接成功")
   except Exception as e:
       print(f"连接失败: {e}")
   ```

2. **认证错误**
   ```python
   # 错误: AuthenticationError: Authentication required
   # 解决: 检查密码设置

   # 正确的密码格式
   redis_url = "redis://:your_password@localhost:6379/0"
   ```

3. **超时错误**
   ```python
   # 错误: TimeoutError: Timeout reading from socket
   # 解决: 调整超时设置

   storage = RedisStorage(
       redis_url=redis_url,
       socket_timeout=30,           # 增加超时时间
       socket_connect_timeout=10,   # 增加连接超时
       retry_on_timeout=True        # 启用超时重试
   )
   ```

4. **内存不足错误**
   ```python
   # 错误: OOM command not allowed when used memory > 'maxmemory'
   # 解决: 清理过期数据或增加内存

   async def cleanup_expired_data(storage):
       async with storage.create_db_session() as conn:
           # 为没有 TTL 的键补充过期时间（非“删除已过期键”；已过期键通常已被 Redis 删除）
           condition = RedisCondition(limit=-1)
           all_keys = await storage.query(conn, "*", condition)

           updated_count = 0
           for key, _value in all_keys:
               ttl_command = RedisCommand(method="ttl", args=(key,))
               ttl = await storage.execute_command(conn, ttl_command)

               if ttl == -1:  # 永不过期（无过期时间）的键
                   expire_command = RedisCommand(method="expire", args=(key, 3600))
                   await storage.execute_command(conn, expire_command)
                   updated_count += 1

           print(f"已为 {updated_count} 个无 TTL 的键设置过期时间")
   ```

### 性能问题诊断

#### 连接池监控
```python
async def diagnose_connection_pool(storage):
    """诊断连接池状态"""
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

#### 内存使用分析
```python
async def analyze_memory_usage(storage):
    """分析内存使用情况"""
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

### 调试模式和日志

#### 详细日志配置
```python
import logging

# 配置 Redis 相关日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 设置特定日志级别
redis_logger = logging.getLogger('redis')
redis_logger.setLevel(logging.INFO)  # 只显示重要信息

# 设置连接日志
connection_logger = logging.getLogger('redis.connection')
connection_logger.setLevel(logging.DEBUG)  # 显示连接详情
```

#### 命令执行跟踪
```python
import time

class DebugRedisStorage(RedisStorage):
    """带调试功能的 Redis 存储"""

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

## 运行示例

### 基本运行
```bash
# 1. 确保 Redis 服务运行
redis-server

# 2. 安装依赖
pip install redis

# 3. 运行完整示例
python examples/storage/redis_example.py

# 4. 运行特定操作示例
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

### 环境配置运行
```bash
# 设置环境变量
export REDIS_HOST=localhost
export REDIS_PORT=6379
export REDIS_DB=0
export REDIS_PASSWORD=your_password
export REDIS_MAX_CONNECTIONS=20

# 运行示例
python examples/storage/redis_example.py
```

### Docker 环境运行
```bash
# 启动 Redis 容器
docker run -d --name redis-test -p 6379:6379 redis:latest

# 运行示例（连接到容器）
REDIS_HOST=localhost python examples/storage/redis_example.py

# 清理容器
docker stop redis-test && docker rm redis-test
```

### 集群环境运行
```bash
# 连接到 Redis 集群
export REDIS_URL="redis://redis-cluster:6379/0"
python examples/storage/redis_example.py

# 或者在代码中指定
python -c "
import asyncio
from examples.storage.redis_example import RedisExampleManager

async def main():
    manager = RedisExampleManager('redis://redis-cluster:6379/0')
    await manager.setup_redis()
    # ... 其他操作
    await manager.close_connection()

asyncio.run(main())
"
```

### 性能测试运行
```bash
# 运行性能测试
python -c "
import asyncio
import time
from examples.storage.redis_example import RedisExampleManager

async def performance_test():
    manager = RedisExampleManager()
    await manager.setup_redis()

    # 测试大量写入
    start_time = time.time()
    for i in range(1000):
        await manager.string_operations_example()

    end_time = time.time()
    print(f'1000 operations completed in {end_time - start_time:.2f} seconds')

    await manager.close_connection()

asyncio.run(performance_test())
"
```
