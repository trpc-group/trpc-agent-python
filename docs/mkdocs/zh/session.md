# Session 会话管理

tRPC-Agent 框架提供了强大的会话（Session）管理功能，用于维护 Agent 与用户交互过程中的对话历史和上下文信息。通过自动持久化对话记录、智能摘要压缩和灵活的存储后端，会话管理为构建有状态的智能 Agent 提供了完整的基础设施。

## Session Service

### 概述

在 trpc-agent 中，`SessionService` 用于管理 `Session`（会话）。`Session` 是一个多轮对话的集合，存储用户与 Agent、Agent 与 Agent 之间的交互记录。

#### Session vs Memory

| 特性 | Session | Memory |
|-----|---------|--------|
| **作用域** | 单个会话（session） | 跨会话（所有 session 共享） |
| **生命周期** | 随会话创建和销毁 | 独立于会话，由 TTL 控制 |
| **存储内容** | 当前会话的完整对话历史 | 关键事件和知识片段 |
| **访问方式** | 自动加载到上下文 | 通过 `load_memory` 工具检索 |
| **典型用途** | 单次对话的上下文 | 长期记忆、用户画像、知识积累 |

---

### Session 的核心结构

基于 [trpc_agent_sdk/sessions/_session.py](../../../trpc_agent_sdk/sessions/_session.py) 的实现，`Session` 包含以下关键字段：

#### 1. 身份标识

- **`id`**：会话 ID，推荐使用 UUID 生成
- **`app_name`**：标识这个对话属于哪个 App
- **`user_id`**：标识这个对话属于哪个 User
- **`save_key`**：格式为 `{app_name}/{user_id}`，用于存储和检索

#### 2. 会话记录（Events）

- **`events`**：`Event` 对象列表，按时间顺序存储
- **事件类型**：用户消息、Agent 响应、工具操作等
- **事件过滤**：支持 TTL 和最大数量限制（`event_ttl_seconds`、`max_events`）

**事件过滤逻辑**（`_session.py`）：
```python
def apply_event_filtering(self, event_ttl_seconds: float = 0.0, max_events: int = 0) -> None:
    """应用事件过滤：TTL 过滤 + 数量限制"""
    # 1. TTL 过滤：删除过期事件
    if event_ttl_seconds > 0:
        cutoff_time = time.time() - event_ttl_seconds
        self.events = [e for e in self.events if e.timestamp >= cutoff_time]

    # 2. 数量限制：只保留最近的 max_events 个事件
    if max_events > 0:
        if len(self.events) > max_events:
            self.events = self.events[-max_events:]

    # 3. 保护第一条用户消息（如果所有事件都被过滤）
    # 确保至少保留一条用户消息，保证对话上下文完整性
```

#### 3. 会话状态（State）

- **`state`**：字典类型，存储会话相关的数据
- **状态作用域**：
  - **Session State**：会话级别状态（存储在 `session.state`）
  - **User State**：用户级别状态（存储在 `SessionService`，键前缀 `user:`）
  - **App State**：应用级别状态（存储在 `SessionService`，键前缀 `app:`）
  - **Temp State**：临时状态（不持久化，键前缀 `temp:`）

**状态合并逻辑**（`_utils.py`）：
```python
def extract_state_delta(state_delta: Optional[dict[str, Any]]) -> StateStorageEntry:
    """提取状态变更，分离为 app、user、session 状态"""
    # 根据键前缀分离状态：
    # - 'app:' 前缀 → app_state_delta
    # - 'user:' 前缀 → user_state_delta
    # - 'temp:' 前缀 → 忽略（不持久化）
    # - 其他 → session_state
```

#### 4. 元数据

- **`last_update_time`**：最后更新时间（时间戳）
- **`conversation_count`**：对话轮数

---

### SessionService 的核心功能

基于 [trpc_agent_sdk/sessions/](../../../trpc_agent_sdk/sessions/) 中的实现，`SessionService` 提供以下核心功能：

#### 1. 会话管理（CRUD）

**创建会话**：
```python
session = await session_service.create_session(
    app_name="my_app",
    user_id="user_001",
    session_id=str(uuid.uuid4()),  # 可选，不提供则自动生成
    state={"initial_key": "initial_value"}  # 可选初始状态
)
```

**获取会话**：
```python
session = await session_service.get_session(
    app_name="my_app",
    user_id="user_001",
    session_id=session_id
)
```

**列出会话**：
```python
session_list = await session_service.list_sessions(
    app_name="my_app",
    user_id="user_001"
)
# 返回 ListSessionsResponse，包含该用户的所有会话（不含 events）
```

**删除会话**：
```python
await session_service.delete_session(
    app_name="my_app",
    user_id="user_001",
    session_id=session_id
)
```

**实现逻辑**（`_base_session_service.py`）：
- `create_session`：创建会话，分离并存储 app/user/session 状态
- `get_session`：获取会话，合并 app/user/session 状态，应用事件过滤
- `list_sessions`：列出会话列表（不包含 events，减少数据传输）
- `delete_session`：删除会话及其关联数据

---

#### 2. 事件追加（Append Event）

**功能**：向会话追加新事件，自动更新状态和 TTL。

**实现逻辑**（`_base_session_service.py`）：
```python
async def append_event(self, session: Session, event: Event) -> Event:
    """追加事件到会话"""
    # 1. 跳过部分事件（partial events）
    if event.partial:
        return event

    # 2. 移除临时状态（temp: 前缀）
    event = self._trim_temp_delta_state(event)

    # 3. 更新会话状态（session.state）
    self.__update_session_state(session, event)

    # 4. 添加事件并应用过滤（TTL + max_events）
    session.add_event(event,
                      event_ttl_seconds=self._session_config.event_ttl_seconds,
                      max_events=self._session_config.max_events)

    # 5. 更新存储（由具体实现处理 app/user 状态）
    return event
```

**状态更新**：
- **Session State**：直接更新 `session.state`
- **User State**：更新 `SessionService` 中的用户状态（键前缀 `user:`）
- **App State**：更新 `SessionService` 中的应用状态（键前缀 `app:`）
- **Temp State**：不持久化，仅存在于内存中

---

#### 3. 事件过滤（Event Filtering）

**功能**：根据 TTL 和最大数量限制过滤事件，避免上下文过长。

**配置**（`SessionServiceConfig`）：
```python
from trpc_agent_sdk.sessions import SessionServiceConfig

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,  # 事件 TTL：1 小时
    max_events=100,          # 最大事件数：100
    num_recent_events=10,    # 保留最近 N 个事件（可选）
)
```

**过滤时机**：
- **追加事件时**：`append_event` 自动应用过滤
- **获取会话时**：`get_session` 自动应用过滤

**过滤逻辑**（`_session.py`）：
1. **TTL 过滤**：删除 `timestamp < (now - event_ttl_seconds)` 的事件
2. **数量限制**：只保留最近的 `max_events` 个事件
3. **保护用户消息**：如果所有事件都被过滤，至少保留第一条用户消息

---

#### 4. TTL（Time-To-Live）缓存淘汰

**功能**：自动清理过期的会话数据，避免存储无限增长。

**TTL 配置**（`SessionServiceConfig`）：
```python
from trpc_agent_sdk.sessions import SessionServiceConfig

session_config = SessionServiceConfig(
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,                    # 启用 TTL
        ttl_seconds=86400,              # 会话过期时间：24 小时
        cleanup_interval_seconds=3600,  # 清理间隔：1 小时（仅 InMemory/SQL）
    ),
)
```

**TTL 刷新机制**：
- **访问时刷新**：`get_session` 时刷新会话 TTL
- **更新时刷新**：`append_event` 时刷新会话 TTL
- **状态访问刷新**：访问 app/user 状态时刷新对应 TTL

**实现差异**：
- **InMemorySessionService**：后台定期清理任务（`_cleanup_loop`）
- **RedisSessionService**：Redis 原生 `EXPIRE` 机制（自动过期）
- **SqlSessionService**：后台定期清理任务（批量 SQL DELETE）

---

#### 5. 状态作用域管理

**功能**：支持不同作用域的状态存储和访问。

**状态作用域**（`_utils.py`）：

| 作用域 | 前缀 | 存储位置 | 生命周期 | 示例 |
|-------|------|---------|---------|------|
| **Session State** | 无前缀 | `session.state` | 随会话 | `{"current_topic": "天气"}` |
| **User State** | `user:` | `SessionService` | 跨会话，用户级别 | `{"user:name": "Alice"}` |
| **App State** | `app:` | `SessionService` | 跨会话，应用级别 | `{"app:version": "1.0"}` |
| **Temp State** | `temp:` | 内存 | 临时，不持久化 | `{"temp:cache": "..."}` |

**状态合并**（`get_session` 时）：
```python
# 从 _in_memory_session_service.py
async def get_session(...) -> Optional[Session]:
    session = self._get_session(app_name, user_id, session_id)
    app_state = self._get_app_state(app_name)      # 获取 app 状态
    user_state = self._get_user_state(app_name, user_id)  # 获取 user 状态

    # 合并状态：session.state + user_state + app_state
    return self._merge_state(app_state, user_state, session)
```

---

#### 6. 会话总结（Session Summarization）

**功能**：将长对话压缩为摘要，减少上下文长度。

**配置**（`SummarizerSessionManager`）：
```python
from trpc_agent_sdk.sessions import SummarizerSessionManager, SessionSummarizer

summarizer = SessionSummarizer(...)
summarizer_manager = SummarizerSessionManager(summarizer=summarizer)

# 设置总结触发条件
set_summarizer_conversation_threshold(summarizer_manager, threshold=10)  # 10 轮对话后总结
set_summarizer_events_count_threshold(summarizer_manager, threshold=50)  # 50 个事件后总结

session_service = InMemorySessionService(summarizer_manager=summarizer_manager)
```

**触发时机**：
- 对话轮数达到阈值
- 事件数量达到阈值
- 时间间隔达到阈值
- 内容长度达到阈值

---

### SessionService 实现

trpc-agent 提供了三种 `SessionService` 实现，方便根据场景选择合适的存储后端：

#### InMemorySessionService

**工作原理**：将所有会话数据直接存储在应用程序的内存中。

**实现特点**（基于 `_in_memory_session_service.py`）：
- **数据结构**：
  - `__sessions`：`dict[app_name, dict[user_id, dict[session_id, SessionWithTTL]]]`
  - `__user_state`：`dict[app_name, dict[user_id, StateWithTTL]]`
  - `__app_state`：`dict[app_name, StateWithTTL]`
- **存储位置**：进程内存
- **TTL 机制**：后台定期清理任务（`_cleanup_loop`）
- **清理方式**：两阶段删除（收集过期项 → 批量删除）

**持久性**：❌ **无**。如果应用程序重启，所有会话数据都会丢失。

**适用场景**：
- ✅ 快速开发
- ✅ 本地测试
- ✅ 示例演示
- ✅ 不需要长期持久性的场景

**配置示例**：
```python
from trpc_agent_sdk.sessions import InMemorySessionService, SessionServiceConfig

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,  # 事件 TTL：1 小时
    max_events=100,          # 最大事件数：100
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # 会话过期时间：24 小时
        cleanup_interval_seconds=3600,  # 清理间隔：1 小时
    ),
)

session_service = InMemorySessionService(session_config=session_config)
```

**注意事项**：
- 清理任务在后台运行，定期删除过期会话和状态
- 如果 `ttl.enable=False`，清理任务不会启动
- 状态合并：`get_session` 时自动合并 app/user/session 状态

**相关示例**：
- 📁 [`examples/session_service_with_in_memory/run_agent.py`](../../../examples/session_service_with_in_memory/run_agent.py) - 完整的 In-Memory Session Service 使用示例

---

#### RedisSessionService

**工作原理**：使用 Redis 存储会话数据，支持多节点共享。

**实现特点**（基于 `_redis_session_service.py`）：
- **数据结构**：Redis Hash（存储 Session JSON）
- **存储位置**：Redis 外部存储
- **键格式**：
  - 会话：`session:{app_name}:{user_id}:{session_id}`
  - 用户状态：`user_state:{app_name}:{user_id}`
  - 应用状态：`app_state:{app_name}`
- **TTL 机制**：Redis 原生 `EXPIRE` 命令（自动过期）
- **TTL 刷新**：访问和更新时自动刷新

**持久性**：✅ **有**。数据持久化到 Redis，应用重启后可以恢复会话。

**适用场景**：
- ✅ 生产环境
- ✅ 需要多节点部署
- ✅ 需要高性能缓存
- ✅ 分布式应用

**配置示例**：
```python
from trpc_agent_sdk.sessions import RedisSessionService, SessionServiceConfig
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

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,
    max_events=100,
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,  # 24 小时过期（Redis 自动处理）
    ),
)

session_service = RedisSessionService(
    db_url=db_url,
    is_async=True,          # 使用异步模式（推荐）
    session_config=session_config,
    **kwargs  # 其他 Redis 连接参数
)
```

**Redis 数据结构**：
```bash
# 会话存储（Redis Hash）
session:weather_app:user_001:session_123
  └─ 字段：JSON 序列化的 Session 对象

# TTL 设置
EXPIRE session:weather_app:user_001:session_123 86400  # 24 小时后过期
```

**注意事项**：
- `is_async=True` 时，使用异步 Redis 客户端，并发场景友好
- `is_async=False` 时，使用同步 Redis 客户端
- Redis 的 `EXPIRE` 机制自动处理过期键，**无需后台清理任务**
- `cleanup_interval_seconds` 参数对 RedisSessionService 无效（Redis 自动过期）

**相关示例**：
- 📁 [`examples/session_service_with_redis/run_agent.py`](../../../examples/session_service_with_redis/run_agent.py) - 完整的 Redis Session Service 使用示例

---

#### SqlSessionService

**工作原理**：将所有会话数据存储到关系型数据库中（MySQL/PostgreSQL）。

**实现特点**（基于 `_sql_session_service.py`）：
- **数据结构**：SQL 表
  - `sessions` 表：存储会话元数据
  - `events` 表：存储会话事件（外键关联）
- **存储位置**：MySQL/PostgreSQL 数据库
- **TTL 机制**：后台定期清理任务（批量 SQL DELETE）
- **清理方式**：用单条 SQL `DELETE` 批量删除过期会话（关联事件由外键级联删除）

**持久性**：✅ **有**。数据持久化到数据库，应用重启后可以恢复会话。

**适用场景**：
- ✅ 生产环境
- ✅ 需要事务安全
- ✅ 需要复杂查询和统计分析
- ✅ 需要数据持久化和备份

**配置示例**：
```python
from trpc_agent_sdk.sessions import SqlSessionService, SessionServiceConfig
import os

# 从环境变量读取 MySQL 配置
db_user = os.environ.get("MYSQL_USER", "root")
db_password = os.environ.get("MYSQL_PASSWORD", "")
db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
db_port = os.environ.get("MYSQL_PORT", "3306")
db_name = os.environ.get("MYSQL_DB", "trpc_agent")

# 构建数据库连接 URL
# 同步操作（pymysql）
db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

# 异步操作（aiomysql）
# db_url = f"mysql+aiomysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,
    max_events=100,
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # 24 小时过期
        cleanup_interval_seconds=3600,  # 1 小时清理一次
    ),
)

session_service = SqlSessionService(
    db_url=db_url,
    is_async=True,          # 使用异步模式（推荐）
    session_config=session_config,
    pool_pre_ping=True,     # 连接健康检查（推荐）
    pool_recycle=3600,      # 连接回收时间：1 小时
)
```

**数据库表结构**：
```sql
-- sessions 表：存储会话元数据
CREATE TABLE sessions (
    app_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    id VARCHAR(255) NOT NULL,
    state JSON,
    conversation_count INT DEFAULT 0,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (app_name, user_id, id),
    INDEX idx_update_time (update_time)  -- 用于清理任务
);

-- events 表：存储会话事件
CREATE TABLE events (
    id VARCHAR(255) NOT NULL,
    app_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255) NOT NULL,
    invocation_id VARCHAR(255),
    author VARCHAR(255),
    content JSON,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- ... 其他字段
    PRIMARY KEY (id, app_name, user_id, session_id),
    FOREIGN KEY (app_name, user_id, session_id)
        REFERENCES sessions(app_name, user_id, id)
        ON DELETE CASCADE,  -- 级联删除
    INDEX idx_timestamp (timestamp)  -- 用于事件过滤
);
```

**清理任务**（批量删除）：
```python
# 从 _sql_session_service.py
async def _cleanup_expired_async(self) -> None:
    """批量删除过期会话和事件"""
    expire_before = datetime.now() - timedelta(seconds=self._session_config.ttl.ttl_seconds)

    # 单条 SQL DELETE 批量删除（级联删除 events）
    DELETE FROM sessions
    WHERE update_time < expire_before;
```

**注意事项**：
- `is_async=True` 时，使用 `aiomysql` 驱动，需要安装：`pip install aiomysql`
- `is_async=False` 时，使用 `pymysql` 驱动，需要安装：`pip install pymysql`
- `pool_pre_ping=True` 推荐启用，避免陈旧连接
- `pool_recycle=3600` 设置连接回收时间，避免长时间连接
- 清理任务使用批量 SQL DELETE，性能优化
- 外键级联删除：删除会话时自动删除关联事件

**相关示例**：
- 📁 [`examples/session_service_with_sql/run_agent.py`](../../../examples/session_service_with_sql/run_agent.py) - 完整的 SQL Session Service 使用示例

---

### 三种实现对比

| 特性 | InMemorySessionService | RedisSessionService | SqlSessionService |
|-----|----------------------|-------------------|------------------|
| **数据存储** | 进程内存 | Redis 外部存储 | MySQL/PostgreSQL |
| **持久化** | ❌ 进程重启丢失 | ✅ 持久化到 Redis | ✅ 持久化到数据库 |
| **分布式** | ❌ 无法跨进程共享 | ✅ 支持跨进程/服务器 | ✅ 支持跨进程/服务器 |
| **TTL 机制** | ✅ 定期清理任务 | ✅ **Redis 自动过期** | ✅ **定期清理任务（批量）** |
| **清理效率** | ⭐⭐⭐ 需要扫描 | ⭐⭐⭐⭐⭐ Redis 原生 | ⭐⭐⭐⭐ **单条 SQL 批量删除** |
| **事务支持** | ❌ | ❌ | ✅ **ACID 事务** |
| **复杂查询** | ❌ | ❌ | ✅ **SQL 查询** |
| **状态管理** | ✅ 内存字典 | ✅ Redis Hash | ✅ SQL 表 |
| **事件存储** | ✅ 内存列表 | ✅ Redis Hash | ✅ SQL 表（外键关联） |
| **部署场景** | 本地开发/单机 | 生产环境/分布式/缓存 | 生产环境/分布式/关系型数据 |
| **性能** | ⭐⭐⭐⭐⭐ 极快 | ⭐⭐⭐⭐ 快 | ⭐⭐⭐ 中等 |

**选择建议**：
- **开发测试** → `InMemorySessionService`（零依赖，快速启动）
- **生产环境（高性能）** → `RedisSessionService`（Redis 自动过期，无后台任务）
- **生产环境（事务/查询）** → `SqlSessionService`（事务安全，支持复杂查询）

---

### 使用示例

#### 基本使用流程

```python
import uuid
from trpc_agent_sdk.sessions import InMemorySessionService, SessionServiceConfig
from trpc_agent_sdk.runners import Runner

# 1. 创建 SessionService
session_config = SessionServiceConfig(
    event_ttl_seconds=3600,  # 事件 TTL：1 小时
    max_events=100,          # 最大事件数：100
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,
        cleanup_interval_seconds=3600,
    ),
)
session_service = InMemorySessionService(session_config=session_config)

# 2. 创建 Runner 并配置 SessionService
runner = Runner(
    app_name="my_app",
    agent=my_agent,
    session_service=session_service
)

# 3. 运行 Agent（如果 Session 不存在，框架会自动创建）
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,  # 可选，不提供则自动生成
    new_message=user_message
):
    # 处理事件...
    pass
```

#### 手动管理会话

```python
import uuid
from trpc_agent_sdk.sessions import InMemorySessionService

session_service = InMemorySessionService()

app_name = "SessionTest"
user_id = "Alice"
session_id = str(uuid.uuid4())

# 创建 Session
session = await session_service.create_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id,
    state={"initial_key": "initial_value"}  # 可选初始状态
)

# 获取 Session
session = await session_service.get_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id
)

# 列出存在的 Session
session_list = await session_service.list_sessions(
    app_name=app_name,
    user_id=user_id
)
print(f"Session: {session_list.sessions}")

# 删除 Session
await session_service.delete_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id
)
```

#### 状态作用域使用

```python
# Session State（会话级别）
session.state["current_topic"] = "天气"

# User State（用户级别，跨会话）
event.actions.state_delta = {
    "user:name": "Alice",        # 用户级别状态
    "user:preference": "dark"    # 用户偏好
}

# App State（应用级别，跨会话）
event.actions.state_delta = {
    "app:version": "1.0",         # 应用版本
    "app:config": {...}           # 应用配置
}

# Temp State（临时状态，不持久化）
event.actions.state_delta = {
    "temp:cache": "..."          # 临时缓存，不存储
}
```

---

### 注意事项

#### 1. 自动创建 Session

**调用 `Runner.run_async` 时，若尚未通过 `create_session` 创建会话，框架会自动创建一个 Session**。

```python
# 不需要手动创建，框架会自动创建
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,  # 可选
    new_message=user_message
):
    pass
```

#### 2. 事件过滤

- `event_ttl_seconds`：事件 TTL，过期事件会被自动删除
- `max_events`：最大事件数，超出限制会删除最旧的事件
- 过滤会保护第一条用户消息，确保对话上下文完整性

#### 3. TTL 配置

- `ttl_seconds`：会话过期时间（秒）
- `cleanup_interval_seconds`：清理间隔（仅 InMemory/SQL，Redis 自动过期）
- 访问和更新时自动刷新 TTL，延长会话有效期

#### 4. 状态作用域

- **Session State**：存储在 `session.state`，随会话生命周期
- **User State**：存储在 `SessionService`，键前缀 `user:`，跨会话共享
- **App State**：存储在 `SessionService`，键前缀 `app:`，跨会话共享
- **Temp State**：不持久化，键前缀 `temp:`，仅存在于内存

#### 5. 并发安全

- `InMemorySessionService`：单进程内线程安全
- `RedisSessionService`：支持多进程/多服务器并发
- `SqlSessionService`：支持多进程/多服务器并发（使用数据库事务）

---

### 相关示例

以下示例展示了不同 SessionService 实现的使用方式：

#### InMemorySessionService

📁 **示例路径**：[examples/session_service_with_in_memory/](../../../examples/session_service_with_in_memory/)

**说明**：
- 演示 In-Memory Session Service 的基本使用
- 展示会话创建、获取、列表、删除
- 演示事件追加和过滤
- 演示状态作用域（Session/User/App State）

**运行方式**：
```bash
cd examples/session_service_with_in_memory/
python3 run_agent.py
```

---

#### RedisSessionService

📁 **示例路径**：[examples/session_service_with_redis/](../../../examples/session_service_with_redis/)

**说明**：
- 演示 Redis Session Service 的使用
- 展示 Redis 自动过期机制
- 提供详细的 Redis 操作指南
- 包含运行结果分析和 Redis 命令示例

**运行方式**：
```bash
cd examples/session_service_with_redis/
python3 run_agent.py
```

---

#### SqlSessionService

📁 **示例路径**：[examples/session_service_with_sql/](../../../examples/session_service_with_sql/)

**说明**：
- 演示 SQL Session Service 的使用
- 展示 MySQL 表结构和数据操作
- 演示批量清理任务
- 提供 MySQL 操作命令和运行结果分析

**运行方式**：
```bash
cd examples/session_service_with_sql/
python3 run_agent.py
```

---

### 核心特性总结

#### 1. 会话管理（CRUD）

- ✅ 创建、获取、列表、删除会话
- ✅ 自动创建会话（Runner 运行时）
- ✅ 会话列表不包含 events（减少数据传输）

#### 2. 事件管理

- ✅ 追加事件到会话
- ✅ 事件过滤（TTL + 最大数量）
- ✅ 保护用户消息（确保上下文完整性）

#### 3. 状态管理

- ✅ 多作用域状态（Session/User/App/Temp）
- ✅ 状态自动合并（`get_session` 时）
- ✅ 状态持久化（User/App 状态跨会话共享）

#### 4. TTL 缓存淘汰

- ✅ 自动清理过期会话，避免存储无限增长
- ✅ 访问和更新时自动刷新 TTL
- ✅ 不同实现使用不同的清理机制

#### 5. 会话总结

- ✅ 支持会话总结（压缩长对话）
- ✅ 可配置触发条件（轮数、事件数、时间间隔等）

#### 6. 灵活的存储后端

- ✅ 支持 In-Memory、Redis、SQL 三种实现
- ✅ 支持 TRPC Redis 集成
- ✅ 可根据场景选择合适的实现

---

### 总结

SessionService 提供了强大的会话管理能力：

- ✅ **会话管理**：完整的 CRUD 操作
- ✅ **事件管理**：追加、过滤、保护用户消息
- ✅ **状态管理**：多作用域状态（Session/User/App/Temp）
- ✅ **TTL 淘汰**：自动清理过期会话
- ✅ **会话总结**：压缩长对话，减少上下文长度
- ✅ **多种实现**：In-Memory、Redis、SQL、TRPC Redis

通过合理使用 SessionService，可以实现：
- 多轮对话上下文管理
- 用户状态持久化
- 应用级状态共享
- 会话生命周期管理

更多详细的使用示例，请参考 [examples/](../../../examples/) 目录中的相关示例。

- [examples/session_service_with_in_memory/run_agent.py](../../../examples/session_service_with_in_memory/run_agent.py)
- [examples/session_service_with_redis/run_agent.py](../../../examples/session_service_with_redis/run_agent.py)
- [examples/session_service_with_sql/run_agent.py](../../../examples/session_service_with_sql/run_agent.py)

---

## State（状态）

在 trpc_agent 中，**State（状态）** 是每个会话（Session）的一个键值对集合，可以理解为会话的「记事本」，存储 Agent 在对话过程中需要记住和引用的动态信息，从而让 Agent 感知会话上下文中的关键信息。

比如下面这些信息，是可以通过State来存储的：
- 用户信息：记住用户偏好（如 `user_theme: 'dark'`）
- 任务进度：追踪多步骤任务状态（如 `booking_step: 'confirm_payment'`）
- 信息积累：构建列表或摘要（如 `shopping_cart: ['book', 'pen']`）
- 中间结果：在 Agent 间传递处理结果（如 `analysis_result: '...'`）

### 特性

#### 数据结构

State 以 key-value 形式存储在 Session 里，也就意味着下面的限制：
- key需要是字符串
- value需要能默认被序列化成字符串（因为将会被注入到Agent的Prompt里）
- 持久化：
    - 如果使用InMemorySessionService，则State在进程重启之后丢失
    - 如果使用RedisSessionService，则State会持久化到Redis里，进程重启/扩容后能恢复会话

#### 作用域控制

State 的key通过使用不同的前缀用来区分不同级别的会话信息。

##### 无前缀（Session State）
- 作用域：当前会话
- 生命周期：随Session的生命周期
- 典型用途：任务进度、临时计算结果
- 示例：`current_step: 'processing'`

##### `user:` 前缀（User State）
- 作用域：特定用户的所有会话
- 生命周期：跨会话持久化
- 典型用途：用户偏好、个人信息
- 示例：`user:language: 'zh'`, `user:name: '张三'`

##### `app:` 前缀（App State）
- 作用域：整个应用的所有用户和会话
- 生命周期：全局持久化
- 典型用途：全局配置、共享资源
- 示例：`app:version: '1.0'`, `app:maintenance_mode: false`

##### `temp:` 前缀（Temporary State）
- 作用域：单次run_async调用
- 生命周期：从不持久化，处理完即丢弃
- 典型用途：中间计算结果、调试信息
- 示例：`temp:api_response: {...}`

### 使用方式

#### 模板引用：在 Instruction 中使用

通过 `{key}` 语法在 Agent 的 `instruction` 中引用状态变量：

```python
LlmAgent(
    name="personalized_assistant",
    model="deepseek-chat",
    instruction="""你好 {user:name}！

当前任务进度：{current_step}
用户偏好语言：{user:language}

根据用户偏好为 {user:name} 提供帮助。""",
)
```

#### 多Agent协作：使用 output_key

Agent 可以将输出自动保存到状态变量：

```python
# Agent 1：分析用户需求
analyzer = LlmAgent(
    name="need_analyzer",
    model="deepseek-chat",
    instruction="分析用户需求并提供详细分析",
    output_key="analysis_result"  # 输出保存到状态
)

# Agent 2：基于分析结果制定方案
planner = LlmAgent(
    name="solution_planner",
    model="deepseek-chat",
    instruction="基于分析结果制定解决方案：\n\n{analysis_result}",
    output_key="solution_plan"
)
```

#### 工具中修改：使用 InvocationContext

在工具函数中通过 `tool_context.state` 修改状态：

**注意：参数名必须为 `tool_context`；若改用其他名字将会出错**

```python
async def update_user_preference(preference: str, value: str,
                                tool_context: InvocationContext) -> str:
    """更新用户偏好设置"""
    # 保存用户级别偏好
    tool_context.state[f"user:{preference}"] = value

    # 记录操作历史（会话级别）
    history = tool_context.state.get("preference_history", [])
    history.append(f"更新{preference}为{value}")
    tool_context.state["preference_history"] = history

    return f"已更新偏好 {preference} = {value}"
```

#### Agent运行前后设置状态

可以通过 `session_service` 在 Agent 运行前后设置初始状态：

```python
# 在Agent执行前，设置初始状态
session = await session_service.create_session(
    app_name="my_app",
    user_id="user123",
    session_id="session456",
    state={
        "user:name": "张三",
        "user:language": "中文",
        "current_step": "started",
        "app:version": "1.0.0"
    }
)

# runner.run_async(...)

# 因为 session 对象是只读的，需要重新 get_session 才能拿到运行后的最新 state
session = await session_service.get_session(app_name="my_app", user_id="user123", session_id="session456")
print(session.state)
# 在Agent运行后，可以修改状态，但注意要更新到session_service才能生效
session.state["current_step"] = "end"
await session_service.update_session(session)
```

### 完整示例

查看完整的 State 使用示例：[examples/session_state/run_agent.py](../../../examples/session_state/run_agent.py)
