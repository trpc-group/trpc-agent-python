# Memory Service 文档

## 概述

`MemoryService` 是 trpc-agent 中用于管理**长期记忆（Long-term Memory）**的核心组件。与 `SessionService` 管理当前会话的上下文不同，`MemoryService` 专注于存储和检索跨会话的历史记忆，帮助 Agent 在后续对话中回忆相关内容。

### Memory vs Session

| 特性 | Session | Memory |
|-----|---------|--------|
| **作用域** | 单个会话（session） | 跨会话（所有 session 共享） |
| **生命周期** | 随会话创建和销毁 | 独立于会话，由 TTL 控制 |
| **存储内容** | 当前会话的完整对话历史 | 关键事件和知识片段 |
| **访问方式** | 自动加载到上下文 | 通过 `load_memory` 工具检索 |
| **典型用途** | 单次对话的上下文 | 长期记忆、用户画像、知识积累 |

---

## MemoryService 的核心功能

基于 `trpc_agent/memory/` 中的实现，MemoryService 提供以下核心功能：

### 1. 存储会话记忆

**功能**：将 Session 中的关键事件存储为长期记忆。

**实现方式**：
- **InMemoryMemoryService**：存储在进程内存的字典中
- **RedisMemoryService**：存储在 Redis List 中（JSON 格式）
- **SqlMemoryService**：存储在 MySQL/PostgreSQL 的 `mem_events` 表中

**代码示例**：
```python
# 存储会话到 Memory
await memory_service.store_session(session=session)
```

**存储逻辑**（以 `InMemoryMemoryService` 为例）：
```python
# from trpc_agent_sdk/memory/_in_memory_memory_service.py
async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
    # 数据结构：{save_key: {session_id: [EventTtl, ...]}}
    self._session_events[session.save_key] = self._session_events.get(session.save_key, {})
    self._session_events[session.save_key][session.id] = [
        EventTtl(event=event, ttl=self._memory_service_config.ttl)
        for event in session.events
        if event.content and event.content.parts  # 只存储有内容的事件
    ]
```

---

### 2. 搜索相关记忆

**功能**：根据查询关键词搜索相关的历史记忆。

**搜索方式**：**关键词匹配**（非语义搜索）

**实现逻辑**（以 `InMemoryMemoryService` 为例）：
```python
# 从 trpc_agent/memory/_in_memory_memory_service.py
async def search_memory(self, key: str, query: str, limit: int = 10, ...) -> SearchMemoryResponse:
    # 1. 提取查询关键词（支持中英文）
    words_in_query = extract_words_lower(query)  # 提取英文单词和中文字符

    # 2. 遍历所有会话事件
    for session_events in self._session_events[key].values():
        for event_ttl in session_events:
            # 3. 提取事件中的关键词
            words_in_event = extract_words_lower(' '.join([part.text for part in event.content.parts if part.text]))

            # 4. 关键词匹配（任意查询词匹配即返回）
            if any(query_word in words_in_event for query_word in words_in_query):
                response.memories.append(MemoryEntry(...))
                # 5. 更新 TTL（访问时刷新过期时间）
                event_ttl.update_expired_at()
```

**关键词提取**（`_utils.py`）：
```python
def extract_words_lower(text: str) -> set[str]:
    """提取英文单词和中文字符"""
    words = set()
    # 提取英文单词（字母序列）
    words.update([word.lower() for word in re.findall(r'[A-Za-z]+', text)])
    # 提取中文字符（Unicode 范围 \u4e00-\u9fff）
    words.update(re.findall(r'[\u4e00-\u9fff]', text))
    return words
```

**使用示例**：
```python
from trpc_agent_sdk.types import SearchMemoryResponse

# 搜索相关记忆
search_key = f"{app_name}/{user_id}"  # 格式：app_name/user_id
response: SearchMemoryResponse = await memory_service.search_memory(
    key=search_key,
    query="天气",  # 查询关键词
    limit=10       # 最多返回 10 条记忆
)

# 处理搜索结果
for memory in response.memories:
    print(f"记忆内容: {memory.content}")
    print(f"作者: {memory.author}")
    print(f"时间: {memory.timestamp}")
```

---

### 3. TTL（Time-To-Live）缓存淘汰

**功能**：自动清理过期的记忆数据，避免内存/存储无限增长。

**实现方式**：
- **InMemoryMemoryService**：后台定期清理任务（`_cleanup_loop`）
- **RedisMemoryService**：Redis 原生 `EXPIRE` 机制（自动过期）
- **SqlMemoryService**：后台定期清理任务（批量 SQL DELETE）

**TTL 配置**：
```python
from trpc_agent_sdk.memory import MemoryServiceConfig

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,                    # 启用 TTL
        ttl_seconds=86400,              # 记忆过期时间：24 小时
        cleanup_interval_seconds=3600,  # 清理间隔：1 小时（仅 InMemory/SQL）
    ),
)
```

**TTL 刷新机制**：
- **访问时刷新**：`search_memory` 时，匹配的事件会刷新 TTL
- **存储时刷新**：`store_session` 时，新事件会设置 TTL

---

### 4. 跨会话共享

**功能**：不同会话（session）可以共享同一份记忆数据。

**实现方式**：
- 使用 `save_key`（格式：`app_name/user_id`）作为记忆的键
- 同一用户的所有会话共享相同的记忆空间
- 搜索时使用 `key=f"{app_name}/{user_id}"` 检索该用户的所有记忆

**数据结构**（InMemoryMemoryService）：
```python
# 数据结构：{save_key: {session_id: [EventTtl, ...]}}
_session_events = {
    "weather_app/user_001": {
        "session_1": [EventTtl(...), EventTtl(...)],
        "session_2": [EventTtl(...), EventTtl(...)],
    },
    "weather_app/user_002": {
        "session_3": [EventTtl(...)],
    }
}
```

---

## MemoryService 实现

trpc-agent 提供了三种 `MemoryService` 实现，方便根据场景选择合适的存储后端：

### InMemoryMemoryService

**工作原理**：将记忆数据直接存储在应用程序的内存中。

**实现特点**（基于 `_in_memory_memory_service.py`）：
- **数据结构**：`dict[str, dict[str, list[EventTtl]]]`（嵌套字典）
- **存储位置**：进程内存
- **搜索方式**：关键词匹配（遍历内存字典）
- **TTL 机制**：后台定期清理任务（`_cleanup_loop`）
- **清理方式**：两阶段删除（收集过期项 → 批量删除）

**持久性**：❌ **无**。如果应用程序重启，所有记忆数据都会丢失。

**适用场景**：
- ✅ 快速开发
- ✅ 本地测试
- ✅ 示例演示
- ✅ 不需要长期持久性的场景

**配置示例**：
```python
from trpc_agent_sdk.memory import InMemoryMemoryService, MemoryServiceConfig

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # 24 小时过期
        cleanup_interval_seconds=3600,  # 1 小时清理一次
    ),
)

memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)
```

**注意事项**：
- `enabled=True` 时，MemoryService 会自动存储 Session 事件，**不需要手动调用 `store_session`**
- 如果 `enabled=False`，MemoryService 不会存储任何数据
- 清理任务在后台运行，定期删除过期事件

**相关示例**：
- 📁 [`examples/memory_service_with_in_memory/run_agent.py`](../../examples/memory_service_with_in_memory/run_agent.py) - 完整的 In-Memory Memory Service 使用示例

---

### RedisMemoryService

**工作原理**：使用 Redis 存储记忆数据，支持多节点共享。

**实现特点**（基于 `_redis_memory_service.py`）：
- **数据结构**：Redis List（`RPUSH` 存储事件 JSON）
- **存储位置**：Redis 外部存储
- **键格式**：`memory:{save_key}:{session_id}`
- **搜索方式**：`KEYS memory:{key}:*` + 关键词匹配
- **TTL 机制**：Redis 原生 `EXPIRE` 命令（自动过期）
- **TTL 刷新**：访问时自动刷新（`search_memory` 时）

**持久性**：✅ **有**。数据持久化到 Redis，应用重启后可以恢复记忆。

**适用场景**：
- ✅ 生产环境
- ✅ 需要多节点部署
- ✅ 需要高性能缓存
- ✅ 分布式应用

**配置示例**：
```python
from trpc_agent_sdk.memory import RedisMemoryService, MemoryServiceConfig
import os

# 从环境变量读取 Redis 配置
db_host = os.environ.get("REDIS_HOST", "127.0.0.1")
db_port = os.environ.get("REDIS_PORT", "6379")
db_password = os.environ.get("REDIS_PASSWORD", "")
db_db = os.environ.get("REDIS_DB", 0)

# 构建 Redis 连接 URL
if db_password:
    db_url = f"redis://:{db_password}@{db_host}:{db_port}/{db_db}"
else:
    db_url = f"redis://{db_host}:{db_port}/{db_db}"

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,  # 24 小时过期（Redis 自动处理）
    ),
)

memory_service = RedisMemoryService(
    db_url=db_url,
    is_async=True,          # 使用异步模式（推荐）
    memory_service_config=memory_service_config,
    enabled=True,
)
```

**Redis 数据结构**：
```bash
# 存储格式：Redis List
memory:weather_app/user_001:session_1
  └─ [0] '{"id":"event_1","author":"user","content":{...},"timestamp":...}'
  └─ [1] '{"id":"event_2","author":"assistant","content":{...},"timestamp":...}'

# TTL 设置
EXPIRE memory:weather_app/user_001:session_1 86400  # 24 小时后过期
```

**注意事项**：
- `is_async=True` 时，使用异步 Redis 客户端，并发场景友好
- `is_async=False` 时，使用同步 Redis 客户端
- Redis 的 `EXPIRE` 机制自动处理过期键，**无需后台清理任务**
- `cleanup_interval_seconds` 参数对 RedisMemoryService 无效（Redis 自动过期）

**相关示例**：
- 📁 [`examples/memory_service_with_redis/run_agent.py`](../../examples/memory_service_with_redis/run_agent.py) - 完整的 Redis Memory Service 使用示例

---

### SqlMemoryService

**工作原理**：将记忆数据存储在关系型数据库中（MySQL/PostgreSQL）。

**实现特点**（基于 `_sql_memory_service.py`）：
- **数据结构**：SQL 表 `mem_events`
- **存储位置**：MySQL/PostgreSQL 数据库
- **搜索方式**：SQL `SELECT` + 关键词匹配
- **TTL 机制**：后台定期清理任务（批量 SQL DELETE）
- **清理方式**：单条 SQL DELETE 批量删除过期事件

**持久性**：✅ **有**。数据持久化到数据库，应用重启后可以恢复记忆。

**适用场景**：
- ✅ 生产环境
- ✅ 需要事务安全
- ✅ 需要复杂查询和统计分析
- ✅ 需要数据持久化和备份

**配置示例**：
```python
from trpc_agent_sdk.memory import SqlMemoryService, MemoryServiceConfig
import os

# 从环境变量读取 MySQL 配置
db_user = os.environ.get("MYSQL_USER", "root")
db_password = os.environ.get("MYSQL_PASSWORD", "")
db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
db_port = os.environ.get("MYSQL_PORT", "3306")
db_name = os.environ.get("MYSQL_DB", "trpc_agent_memory")

# 构建数据库连接 URL
# 同步操作（pymysql）
db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

# 异步操作（aiomysql）
# db_url = f"mysql+aiomysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # 24 小时过期
        cleanup_interval_seconds=3600,  # 1 小时清理一次
    ),
)

memory_service = SqlMemoryService(
    db_url=db_url,
    is_async=True,          # 使用异步模式（推荐）
    memory_service_config=memory_service_config,
    enabled=True,
    pool_pre_ping=True,     # 连接健康检查（推荐）
    pool_recycle=3600,      # 连接回收时间：1 小时
)
```

**数据库表结构**：
```sql
CREATE TABLE mem_events (
    id VARCHAR(255) NOT NULL,              -- 事件 UUID
    save_key VARCHAR(255) NOT NULL,        -- app_name/user_id
    session_id VARCHAR(255) NOT NULL,       -- 会话 ID
    invocation_id VARCHAR(255),            -- 调用 ID
    author VARCHAR(255),                    -- 作者（user/assistant）
    content JSON,                          -- 事件内容（JSON）
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 创建时间
    -- ... 其他字段
    PRIMARY KEY (id, save_key, session_id),
    INDEX idx_save_key (save_key),         -- 用于检索
    INDEX idx_timestamp (timestamp)        -- 用于清理任务
);
```

**清理任务**（批量删除）：
```python
# 从 _sql_memory_service.py
async def _cleanup_expired_async(self) -> None:
    """批量删除过期事件"""
    expire_before = datetime.now() - timedelta(seconds=self._memory_service_config.ttl.ttl_seconds)

    # 单条 SQL DELETE 批量删除
    DELETE FROM mem_events
    WHERE timestamp < expire_before;
```

**注意事项**：
- `is_async=True` 时，使用 `aiomysql` 驱动，需要安装：`pip install aiomysql`
- `is_async=False` 时，使用 `pymysql` 驱动，需要安装：`pip install pymysql`
- `pool_pre_ping=True` 推荐启用，避免陈旧连接
- `pool_recycle=3600` 设置连接回收时间，避免长时间连接
- 清理任务使用批量 SQL DELETE，性能优化

**相关示例**：
- 📁 [`examples/memory_service_with_sql/run_agent.py`](../../examples/memory_service_with_sql/run_agent.py) - 完整的 SQL Memory Service 使用示例

---

### TrpcRedisMemoryService

**工作原理**：使用 TRPC Redis 客户端存储记忆数据，可以对接 TRPC 的生态插件。

**实现特点**：
- **存储方式**：与 `RedisMemoryService` 相同（Redis List）
- **客户端**：TRPC Redis 客户端（而非直接使用 redis-py）
- **优势**：支持 TRPC 服务发现、负载均衡、监控告警等

**持久性**：✅ **有**。数据持久化到 Redis。

**适用场景**：
- ✅ 企业级生产环境
- ✅ 已有 TRPC 项目
- ✅ 需要服务发现和负载均衡
- ✅ 需要完善的监控和告警

**安装依赖**：
```bash
pip install trpc-agent[redis] --extra-index-url https://mirrors.tencent.com/repository/pypi/tencent_pypi/simple/
```

**配置示例**：

**1. 配置 `trpc_python.yaml`**：
```yaml
client:                                            # 客户端调用的后端配置
  timeout: 1000                                    # 针对所有后端的请求最长处理时间
  namespace: Development                           # 针对所有后端的环境
  service:                                         # 针对单个后端的配置
    - name: trpc.redis.test_example                # 后端服务的 service name
      target: ip://127.0.0.1:6379
      timeout: 5000                                # 当前请求最长处理时间
      password: ${REDIS_PASSWORD}
      redis:
        db: 0
```

**2. 代码中使用**：
```python
import os
from trpc.config import config
from trpc.plugin import setup
from trpc_agent_sdk.server.memory_service.trpc_redis_memory_service import TrpcRedisMemoryService
from trpc_agent_sdk.memory import MemoryServiceConfig

# 加载 trpc-python 配置及环境
config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "trpc_python.yaml"))
config.load_global_config(config_path, 'utf-8')
setup()

# 创建 TrpcRedisMemoryService
memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,  # 24 小时过期
    ),
)

memory_service = TrpcRedisMemoryService(
    name="trpc.redis.test_example",  # 与 trpc_python.yaml 中的 service name 对应
    memory_service_config=memory_service_config,
    enabled=True,
)
```

**注意事项**：
- `name` 参数必须与 `trpc_python.yaml` 中的 `service.name` 对应
- 需要先加载 TRPC 配置（`config.load_global_config` 和 `setup()`）
- TTL 机制与 `RedisMemoryService` 相同（Redis 自动过期）

**相关示例**：
- 📁 [`examples/trpc_redis_memory_service/trpc_main.py`](../../examples/trpc_redis_memory_service/trpc_main.py) - 完整的 TRPC Redis Memory Service 使用示例

---

## 三种实现对比

| 特性 | InMemoryMemoryService | RedisMemoryService | SqlMemoryService |
|-----|----------------------|-------------------|------------------|
| **数据存储** | 进程内存 | Redis 外部存储 | MySQL/PostgreSQL |
| **持久化** | ❌ 进程重启丢失 | ✅ 持久化到 Redis | ✅ 持久化到数据库 |
| **分布式** | ❌ 无法跨进程共享 | ✅ 支持跨进程/服务器 | ✅ 支持跨进程/服务器 |
| **TTL 机制** | ✅ 定期清理任务 | ✅ **Redis 自动过期** | ✅ **定期清理任务（批量）** |
| **清理效率** | ⭐⭐⭐ 需要扫描 | ⭐⭐⭐⭐⭐ Redis 原生 | ⭐⭐⭐⭐ **单条 SQL 批量删除** |
| **事务支持** | ❌ | ❌ | ✅ **ACID 事务** |
| **复杂查询** | ❌ | ❌ | ✅ **SQL 查询** |
| **部署场景** | 本地开发/单机 | 生产环境/分布式/缓存 | 生产环境/分布式/关系型数据 |
| **性能** | ⭐⭐⭐⭐⭐ 极快 | ⭐⭐⭐⭐ 快 | ⭐⭐⭐ 中等 |

**选择建议**：
- **开发测试** → `InMemoryMemoryService`（零依赖，快速启动）
- **生产环境（高性能）** → `RedisMemoryService`（Redis 自动过期，无后台任务）
- **生产环境（事务/查询）** → `SqlMemoryService`（事务安全，支持复杂查询）
- **企业级（TRPC 生态）** → `TrpcRedisMemoryService`（服务发现、监控告警）

---

## 使用示例

### 基本使用流程

```python
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.memory import InMemoryMemoryService, MemoryServiceConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.types import Content, Part

# 1. 创建 MemoryService
memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,
        cleanup_interval_seconds=3600,
    ),
)
memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)

# 2. 创建 SessionService
session_service = InMemorySessionService()

# 3. 创建 Runner 并配置服务
runner = Runner(
    app_name="my_app",
    agent=my_agent,
    session_service=session_service,
    memory_service=memory_service  # 配置 MemoryService
)

# 4. 运行 Agent（MemoryService 会自动存储事件）
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_message
):
    # 处理事件...
    pass

# 5. 搜索相关记忆（通过 load_memory 工具）
# Agent 会自动调用 memory_service.search_memory()
```

### 手动存储和搜索

```python
# 手动存储会话到 Memory
session = await session_service.get_session(
    app_name="my_app",
    user_id=user_id,
    session_id=session_id
)
if session:
    await memory_service.store_session(session=session)

# 手动搜索记忆
search_key = f"{app_name}/{user_id}"
response = await memory_service.search_memory(
    key=search_key,
    query="用户的名字",
    limit=10
)

for memory in response.memories:
    print(f"记忆: {memory.content}")
```

---

## 集成 SessionService 和 MemoryService

在实际应用中，通常需要同时使用 `SessionService` 和 `MemoryService`：

```python
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.memory import InMemoryMemoryService, MemoryServiceConfig
from trpc_agent_sdk.runners import Runner

# 创建服务实例
session_service = InMemorySessionService()
memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,
    ),
)
memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)

# 创建 Runner 并配置服务
runner = Runner(
    app_name="my_app",
    agent=my_agent,
    session_service=session_service,
    memory_service=memory_service  # 可选：配置 MemoryService
)

# 运行 Agent
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_message
):
    # 处理事件...
    pass
```

**工作流程**：

1. **SessionService** 管理当前会话的上下文（对话历史、状态等）
2. **MemoryService** 自动存储 Session 事件到长期记忆（如果 `enabled=True`）
3. **load_memory 工具** 调用 `memory_service.search_memory()` 检索相关记忆
4. Agent 可以同时访问当前会话上下文和历史记忆，提供更连贯的对话体验

---

## 相关示例

以下示例展示了不同 MemoryService 实现的使用方式：

### InMemoryMemoryService

📁 **示例路径**：[`examples/memory_service_with_in_memory/run_agent.py`](../../examples/memory_service_with_in_memory/run_agent.py)

**说明**：
- 演示 In-Memory Memory Service 的基本使用
- 展示跨会话记忆共享
- 演示 TTL 缓存淘汰机制
- 包含详细的运行结果分析

**运行方式**：
```bash
cd examples/memory_service_with_in_memory/
python3 run_agent.py
```

---

### RedisMemoryService

📁 **示例路径**：[`examples/memory_service_with_redis/run_agent.py`](../../examples/memory_service_with_redis/run_agent.py)

**说明**：
- 演示 Redis Memory Service 的使用
- 展示 Redis 自动过期机制
- 提供详细的 Redis 操作指南
- 包含运行结果分析和 Redis 命令示例

**运行方式**：
```bash
cd examples/memory_service_with_redis/
python3 run_agent.py
```

---

### SqlMemoryService

📁 **示例路径**：[`examples/memory_service_with_sql/run_agent.py`](../../examples/memory_service_with_sql/run_agent.py)

**说明**：
- 演示 SQL Memory Service 的使用
- 展示 MySQL 表结构和数据操作
- 演示批量清理任务
- 提供 MySQL 操作命令和运行结果分析

**运行方式**：
```bash
cd examples/memory_service_with_sql/
python3 run_agent.py
```

---

### TrpcRedisMemoryService

📁 **示例路径**：[`examples/trpc_redis_memory_service/trpc_main.py`](../../examples/trpc_redis_memory_service/trpc_main.py)

**说明**：
- 演示 TRPC Redis Memory Service 的使用
- 展示 TRPC 框架集成
- 演示 HTTP SSE 流式响应
- 提供 TRPC 配置和测试方法

**运行方式**：
```bash
# 终端1：启动 TRPC 服务
cd examples/trpc_redis_memory_service/
python3 trpc_main.py

# 终端2：运行测试客户端
python3 test_service_rpc.py
```

---

## 核心特性总结

### 1. 跨会话记忆共享

- ✅ 不同会话可以访问同一份记忆数据
- ✅ 使用 `save_key`（`app_name/user_id`）作为记忆键
- ✅ 适合存储用户画像、长期偏好等跨会话信息

### 2. 关键词搜索

- ✅ 支持中英文关键词提取和匹配
- ✅ 使用 `extract_words_lower` 提取英文单词和中文字符
- ✅ 匹配逻辑：任意查询词匹配即返回

### 3. TTL 缓存淘汰

- ✅ 自动清理过期记忆，避免存储无限增长
- ✅ 访问时刷新 TTL（`search_memory` 时）
- ✅ 不同实现使用不同的清理机制

### 4. 自动存储

- ✅ `enabled=True` 时，MemoryService 自动存储 Session 事件
- ✅ 无需手动调用 `store_session`（除非需要特殊控制）
- ✅ 只存储有内容的事件（`event.content and event.content.parts`）

### 5. 灵活的存储后端

- ✅ 支持 In-Memory、Redis、SQL 三种实现
- ✅ 支持 TRPC Redis 集成
- ✅ 可根据场景选择合适的实现

---

## 注意事项

### 1. enabled 参数

- `enabled=True`：MemoryService 会自动存储 Session 事件，**不需要手动调用 `store_session`**
- `enabled=False`：MemoryService 不会存储任何数据，`store_session` 和 `search_memory` 都不会生效

### 2. 关键词搜索限制

- 当前实现使用**关键词匹配**，而非语义搜索
- 查询词必须与事件文本中的词完全匹配
- 适合快速原型开发，不适合复杂的语义检索需求

### 3. TTL 配置

- `ttl_seconds`：记忆过期时间（秒）
- `cleanup_interval_seconds`：清理间隔（仅 InMemory/SQL，Redis 自动过期）
- 访问时自动刷新 TTL，延长记忆有效期

### 4. 并发安全

- `InMemoryMemoryService`：单进程内线程安全
- `RedisMemoryService`：支持多进程/多服务器并发
- `SqlMemoryService`：支持多进程/多服务器并发（使用数据库事务）

---

## 总结

MemoryService 提供了强大的长期记忆管理能力：

- ✅ **跨会话共享**：不同会话可以访问共享的记忆
- ✅ **自动存储**：`enabled=True` 时自动存储 Session 事件
- ✅ **关键词搜索**：支持中英文关键词匹配
- ✅ **TTL 淘汰**：自动清理过期记忆
- ✅ **多种实现**：In-Memory、Redis、SQL、TRPC Redis

通过合理使用 MemoryService，可以实现：
- 用户画像构建
- 长期偏好记忆
- 跨会话知识共享
- 智能对话上下文

更多详细的使用示例，请参考 `examples/` 目录中的相关示例。

- [examples/memory_service_with_in_memory/run_agent.py](../../examples/memory_service_with_in_memory/run_agent.py)
- [examples/memory_service_with_redis/run_agent.py](../../examples/memory_service_with_redis/run_agent.py)
- [examples/memory_service_with_sql/run_agent.py](../../examples/memory_service_with_sql/run_agent.py)
