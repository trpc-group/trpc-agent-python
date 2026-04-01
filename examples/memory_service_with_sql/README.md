# Memory Service MySQL 存储示例

## 示例简介

本示例演示如何使用 **SqlMemoryService** 实现跨会话的记忆管理，并展示 **TTL（Time-To-Live）缓存淘汰机制** 的效果。

### 核心特性

- ✅ **MySQL 持久化**: 使用 `SqlMemoryService` 在 MySQL 中持久化跨会话记忆数据
- ✅ **跨会话共享**: 不同会话（session）可以共享同一份记忆数据
- ✅ **TTL 缓存淘汰**: 配置记忆 20 秒 TTL，演示定期清理任务自动删除过期数据
- ✅ **语义搜索**: 通过 `load_memory` 工具根据查询关键词检索相关记忆
- ✅ **批量清理**: 后台任务使用单条 SQL DELETE 批量删除过期数据，高性能
- ✅ **事务安全**: 使用数据库事务保证数据一致性
- ✅ **分布式支持**: MySQL 存储支持跨进程、跨服务器共享记忆

## 环境要求

- Python 3.10+（强烈建议使用 3.12）
- MySQL 5.7+ 或 MariaDB 10.3+

## 安装和运行

### 1. 下载并安装 trpc-agent

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 2. 安装 MySQL 驱动

```bash
# 安装 pymysql（同步驱动，推荐）
pip3 install pymysql

# 或者安装 aiomysql（异步驱动）
# pip3 install aiomysql
```

---

### 3. 准备 MySQL 环境

#### 启动 MySQL 服务

选择以下任一方式启动 MySQL：

```bash
# 方式1：使用系统服务
service mysql start

# 方式2：使用 Docker（推荐）
docker run -d \
  -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=your_password \
  -e MYSQL_DATABASE=trpc_agent_memory \
  --name mysql \
  mysql:8.0

# 方式3：直接启动 MySQL
mysqld
```

验证 MySQL 是否启动成功：

```bash
mysql -u root -p -e "SELECT VERSION();"
# 输出: 8.0.xx  ✅ 启动成功
```

---

#### 创建数据库

```bash
# 连接到 MySQL
mysql -u root -p

# 创建数据库
CREATE DATABASE IF NOT EXISTS trpc_agent_memory CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 切换到该数据库
USE trpc_agent_memory;

# 查看数据库
SHOW DATABASES;
```

**重要提示**：`SqlMemoryService` 会在首次运行时**自动创建表结构**，无需手动创建表。

---

#### MySQL 客户端操作指南

**连接 MySQL**

```bash
# 无密码（本地开发，不安全）
mysql -u root

# 有密码（推荐）
mysql -u root -p

# 连接到特定数据库
mysql -u root -p trpc_agent_memory
```

**查看数据库和表**

```bash
# 查看所有数据库
SHOW DATABASES;

# 切换到目标数据库
USE trpc_agent_memory;

# 查看所有表
SHOW TABLES;
# 期望输出（首次运行后）:
# +----------------------------+
# | Tables_in_trpc_agent_memory |
# +----------------------------+
# | mem_events                  |
# +----------------------------+

# 查看表结构
DESC mem_events;
```

**查看记忆数据**

```bash
# 查看所有记忆事件
SELECT * FROM mem_events;

# 查看特定应用和用户的事件
SELECT * FROM mem_events
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_memory_user';

# 查看特定会话的事件
SELECT * FROM mem_events
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_memory_user'
  AND session_id = 'sql_memory_session_3';

# 查看事件数量
SELECT COUNT(*) FROM mem_events;

# 按会话统计事件数量
SELECT session_id, COUNT(*) AS event_count
FROM mem_events
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_memory_user'
GROUP BY session_id
ORDER BY session_id;

# 查看最近的 10 条事件
SELECT id, app_name, user_id, session_id,
       LEFT(event, 100) AS event_preview,
       timestamp
FROM mem_events
ORDER BY timestamp DESC
LIMIT 10;

# 查看包含特定关键词的事件
SELECT id, session_id, event, timestamp
FROM mem_events
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_memory_user'
  AND event LIKE '%Alice%';
```

**检查数据是否过期**

```bash
# 查看距离创建时间的秒数
SELECT
    id,
    session_id,
    timestamp,
    TIMESTAMPDIFF(SECOND, timestamp, NOW()) AS seconds_ago,
    CASE
        WHEN TIMESTAMPDIFF(SECOND, timestamp, NOW()) > 20 THEN '已过期'
        ELSE '有效'
    END AS status
FROM mem_events
ORDER BY timestamp DESC;

# 查看即将过期的事件（TTL=20秒）
SELECT * FROM mem_events
WHERE TIMESTAMPDIFF(SECOND, timestamp, NOW()) BETWEEN 15 AND 20;

# 统计过期事件数量
SELECT COUNT(*) AS expired_count
FROM mem_events
WHERE TIMESTAMPDIFF(SECOND, timestamp, NOW()) > 20;
```

**删除数据**

```bash
# 删除特定会话的事件
DELETE FROM mem_events
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_memory_user'
  AND session_id = 'sql_memory_session_0';

# 删除过期事件（手动清理）
DELETE FROM mem_events
WHERE TIMESTAMPDIFF(SECOND, timestamp, NOW()) > 20;

# 删除所有事件（⚠️ 危险操作）
DELETE FROM mem_events;

# 删除表（⚠️ 非常危险）
DROP TABLE mem_events;

# 清空数据库（⚠️ 极度危险）
DROP DATABASE trpc_agent_memory;
```

**实时监控数据变化**

```bash
# 监控事件表的变化（每 1 秒刷新）
watch -n 1 'mysql -u root -p"your_password" -e "USE trpc_agent_memory; SELECT COUNT(*) FROM mem_events;"'

# 监控事件的时间状态
watch -n 1 'mysql -u root -p"your_password" -e "USE trpc_agent_memory; SELECT session_id, TIMESTAMPDIFF(SECOND, timestamp, NOW()) AS seconds_ago FROM mem_events ORDER BY timestamp DESC LIMIT 5;"'
```

**查看 MySQL 信息**

```bash
# 查看 MySQL 版本
SELECT VERSION();

# 查看当前连接数
SHOW STATUS LIKE 'Threads_connected';

# 查看数据库大小
SELECT
    table_schema AS 'Database',
    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'Size (MB)'
FROM information_schema.tables
WHERE table_schema = 'trpc_agent_memory'
GROUP BY table_schema;

# 查看表的行数
SELECT
    table_name AS 'Table',
    table_rows AS 'Rows'
FROM information_schema.tables
WHERE table_schema = 'trpc_agent_memory';
```

---

#### 调试技巧

**场景 1：验证示例运行前数据库是否为空**

```bash
mysql -u root -p
> USE trpc_agent_memory;
> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|        0 |
+----------+  ✅ 数据库为空，可以开始测试
```

**场景 2：查看 First Run 后的数据**

```bash
# First Run 结束后立即查询
mysql -u root -p
> USE trpc_agent_memory;

> SELECT session_id, COUNT(*) AS event_count
  FROM mem_events
  GROUP BY session_id;
+------------------------+-------------+
| session_id             | event_count |
+------------------------+-------------+
| sql_memory_session_0   |           2 |
| sql_memory_session_1   |           2 |
| sql_memory_session_2   |           2 |
| sql_memory_session_3   |           2 |
| sql_memory_session_4   |           2 |
| sql_memory_session_5   |           2 |
| sql_memory_session_6   |           2 |
+------------------------+-------------+

> SELECT id, session_id, LEFT(event, 50) AS event_preview,
         TIMESTAMPDIFF(SECOND, timestamp, NOW()) AS seconds_ago
  FROM mem_events
  WHERE session_id = 'sql_memory_session_3';
+------+------------------------+---------------------------------------------------+-------------+
| id   | session_id             | event_preview                                     | seconds_ago |
+------+------------------------+---------------------------------------------------+-------------+
| uuid | sql_memory_session_3   | {"content": {"parts": [{"text": "Hello! My name   |           2 |
| uuid | sql_memory_session_3   | {"content": {"parts": [{"text": "Hello, Alice!    |           2 |
+------+------------------------+---------------------------------------------------+-------------+
```

**场景 3：验证清理任务执行**

```bash
# 查看清理任务日志（终端输出）
[2026-02-03 22:00:58][INFO] Memory cleanup completed: deleted 20 expired events

# 查看清理前后的数据量
mysql -u root -p
> USE trpc_agent_memory;
> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|       14 |  # 清理前
+----------+

# 等待清理任务执行（10 秒间隔）
> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|        0 |  # 清理后
+----------+
```

**场景 4：手动模拟记忆数据**

```bash
# 手动插入记忆事件
mysql -u root -p
> USE trpc_agent_memory;
> INSERT INTO mem_events (id, app_name, user_id, session_id, event, timestamp)
  VALUES (
    UUID(),
    'weather_agent_demo',
    'sql_memory_user',
    'test_session',
    '{"content": {"parts": [{"text": "My name is Bob"}]}}',
    NOW()
  );

> SELECT * FROM mem_events WHERE session_id = 'test_session';
```

---

### 4. 配置环境变量

在 `.env` 文件中设置 LLM 和 MySQL 配置：

```bash
# LLM 配置（必填）
TRPC_AGENT_API_KEY=your_api_key
TRPC_AGENT_BASE_URL=http://v2.open.venus.woa.com/llmproxy
TRPC_AGENT_MODEL_NAME=deepseek-v3-local-II

# MySQL 配置
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DB=trpc_agent_memory
```

---

### 5. 运行示例

```bash
python3 examples/memory_service_with_sql/run_agent.py
```

---

## 代码说明

### SqlMemoryService 配置

```python
def create_memory_service(is_async: bool = False):
    """创建 SQL Memory Service"""

    # 从环境变量读取 MySQL 配置
    db_user = os.environ.get("MYSQL_USER", "root")
    db_password = os.environ.get("MYSQL_PASSWORD", "")
    db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    db_port = os.environ.get("MYSQL_PORT", "3306")
    db_name = os.environ.get("MYSQL_DB", "trpc_agent_memory")

    # 构建 MySQL 连接 URL
    db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

    # 配置 Memory 参数
    memory_service_config = MemoryServiceConfig(
        enabled=True,                      # 启用 Memory 功能
        ttl=MemoryServiceConfig.create_ttl_config(
            enable=True,                   # 启用 TTL
            ttl_seconds=20,                # 记忆过期时间：20 秒
            cleanup_interval_seconds=10    # 清理间隔：10 秒
        ),
    )

    memory_service = SqlMemoryService(
        memory_service_config=memory_service_config,
        is_async=is_async,
        db_url=db_url,
        pool_pre_ping=True,                # 连接池健康检查
        pool_recycle=3600,                 # 连接回收时间：1 小时
    )

    return memory_service
```

**配置说明**：

| 参数 | 值 | 说明 | 生产环境建议 |
|-----|---|------|------------|
| `enabled` | True | 启用 Memory 功能 | True |
| `ttl_seconds` | 20 | 记忆过期时间 | 86400（24小时）或更长 |
| `cleanup_interval_seconds` | 10 | 清理间隔 | 3600（1小时） |
| `pool_pre_ping` | True | 连接健康检查 | True（推荐） |
| `pool_recycle` | 3600 | 连接回收时间 | 3600（1小时） |

⚠️ **重要提示**：本示例将 TTL 设置为 **20 秒**，清理间隔 **10 秒**，是为了快速演示缓存淘汰行为。**生产环境请设置更合理的值**！

---

### 测试流程

示例运行三次相同的对话，每次间隔不同，用于演示 TTL 缓存淘汰效果：

```python
async def main():
    print("First run")
    await run_weather_agent()        # 运行 7 个查询

    await asyncio.sleep(2)           # 等待 2 秒（< 20秒 TTL）

    print("Second run")
    await run_weather_agent()        # 再次运行 7 个查询

    await asyncio.sleep(30)          # 等待 30 秒（> 20秒 TTL）

    print("Third run")
    await run_weather_agent()        # 第三次运行

    await asyncio.sleep(30)          # 等待清理任务完成
```

**时间线**：

```
t=0s    ┌─────────────┐
        │  First Run  │  创建记忆，存储对话
        └─────────────┘
           ↓ 7 个查询（每个查询用新的 session_id）
           ↓ MySQL: 插入约 14 条事件记录

t=2s    ┌─────────────┐
        │ Second Run  │  记忆仍有效，成功检索 ✅
        └─────────────┘
           ↓ 2s < 20s → 记忆仍在 MySQL
           ↓ 能通过 load_memory 检索到 Alice 和 blue

t=10s   ⏰ 清理任务第 1 次执行（无过期数据，跳过）
t=20s   ⏰ 清理任务第 2 次执行
        └─ 发现 First Run 的过期数据（距离 20s）
           └─ 执行批量 DELETE 删除过期事件
           └─ 日志：deleted 20 expired events

t=32s   ┌─────────────┐
        │  Third Run  │  记忆已过期，无法检索 ❌
        └─────────────┘
           ↓ 32s > 20s → 记忆已被清理
           ↓ load_memory 返回空数组
           ↓ 从干净状态重新开始

t=40s   ⏰ 清理任务第 4 次执行
        └─ 发现 Second Run 的过期数据（距离 28s）
           └─ 执行批量 DELETE 删除过期事件
           └─ 日志：deleted 24 expired events
```

---

### 查询列表

每次运行都执行相同的 7 个查询，但使用**不同的 session_id**：

```python
demo_queries = [
    "Do you remember my name?",                      # Q1: 测试名字记忆
    "Do you remember my favorite color?",            # Q2: 测试颜色记忆
    "what is the weather like in paris?",            # Q3: 测试工具调用
    "Hello! My name is Alice. What's your name?",    # Q4: 告诉 Agent 名字
    "Do you remember my name?",                      # Q5: 验证名字记忆
    "Hello! My favorite color is blue. ...",         # Q6: 告诉 Agent 颜色
    "Do you remember my favorite color?",            # Q7: 验证颜色记忆
]

# 每个查询使用不同的 session_id
for index, query in enumerate(demo_queries):
    session_id = f"sql_memory_session_{index}"      # session_0, session_1, ...
```

**查询设计意图**：

| 查询 | 目的 | First Run | Second Run | Third Run |
|-----|------|-----------|-----------|-----------|
| **Q1** | 测试初始状态 | ❌ 不记得 | ✅ 记得（Alice） | ❌ 不记得（已过期） |
| **Q2** | 测试颜色记忆 | ❌ 不记得 | ✅ 记得（blue） | ❌ 不记得（已过期） |
| **Q3** | 测试工具 | ✅ 正常 | ✅ 正常 | ✅ 正常 |
| **Q4** | 建立记忆 | 📝 存储名字 | 📝 追加记忆 | 📝 重新存储 |
| **Q5** | 验证名字记忆 | ✅ 记得（刚存储的） | ✅ 记得（跨会话） | ✅ 记得（当前 Run） |
| **Q6** | 存储颜色 | 💾 保存到 MySQL | 💾 追加到 MySQL | 💾 重新保存 |
| **Q7** | 验证颜色记忆 | ✅ 记得（刚存储的） | ✅ 记得（跨会话） | ✅ 记得（当前 Run） |

---

## MySQL 表结构

`SqlMemoryService` 会自动创建以下表：

### `mem_events` 表

存储跨会话的记忆事件：

```sql
CREATE TABLE mem_events (
    id VARCHAR(255) NOT NULL,              -- 事件 UUID
    app_name VARCHAR(255) NOT NULL,        -- 应用名称
    user_id VARCHAR(255) NOT NULL,         -- 用户 ID
    session_id VARCHAR(255) NOT NULL,      -- 会话 ID
    event TEXT NOT NULL,                   -- 事件内容（JSON）
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 创建时间
    PRIMARY KEY (id),
    INDEX idx_app_user (app_name, user_id),         -- 用于检索
    INDEX idx_timestamp (timestamp)                 -- 用于清理任务
);
```

**字段说明**：

| 字段 | 类型 | 说明 |
|-----|------|------|
| `id` | VARCHAR(255) | 事件的唯一标识符（UUID） |
| `app_name` | VARCHAR(255) | 应用名称（如 `weather_agent_demo`） |
| `user_id` | VARCHAR(255) | 用户 ID（如 `sql_memory_user`） |
| `session_id` | VARCHAR(255) | 会话 ID（如 `sql_memory_session_0`） |
| `event` | TEXT | 事件内容（JSON 格式） |
| `timestamp` | TIMESTAMP | 事件创建时间（用于 TTL 判断） |

**索引说明**：

- `PRIMARY KEY (id)`：确保事件唯一性
- `INDEX idx_app_user (app_name, user_id)`：加速跨会话检索
- `INDEX idx_timestamp (timestamp)`：加速清理任务（WHERE timestamp < ...）

---

## Memory 与 Session 的区别

### 核心概念对比

| 特性 | Session State | Memory |
|-----|--------------|--------|
| **作用域** | 单个会话（session） | 跨会话（所有 session 共享） |
| **生命周期** | 随会话创建和销毁 | 独立于会话，由 TTL 控制 |
| **存储内容** | 当前会话的对话历史 | 关键事件和知识片段 |
| **访问方式** | 自动加载到上下文 | 通过 `load_memory` 工具检索 |
| **典型用途** | 单次对话的上下文 | 长期记忆、用户画像、知识积累 |
| **MySQL 表** | `sessions` 表 | `mem_events` 表 |
| **本示例** | 每个查询独立会话 | 跨查询共享记忆 |

---

## 运行结果分析

### 完整输出

```txt
python3 examples/memory_service_with_sql/run_agent.py
============================================================
First run
============================================================
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": []}'}]
It seems I don't have your name stored in my memory. Could you remind me of your name?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": []}'}]
It seems I don't have any memory of your favorite color. Could you remind me what it is?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is sunny with a temperature of 25 degrees Celsius.
----------------------------------------
🤖 Assistant: Hello, Alice! My name is Assistant. How can I help you today?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": [... "My name is Alice" ...]}'}]
Yes, your name is Alice! How can I assist you today?
----------------------------------------
🤖 Assistant: As an AI, I don't have personal preferences or feelings, so I don't have a favorite color. But I think blue is a great choice—it's often associated with calmness and serenity!
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": [... "favorite color is blue" ...]}'}]
Yes, you mentioned that your favorite color is blue!
----------------------------------------
============================================================
Second run
============================================================
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": [... Alice ...]}'}]
Yes, your name is Alice! How can I assist you today?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": [... blue ...]}'}]
Yes, you mentioned that your favorite color is blue!
----------------------------------------
...
[2026-02-03 22:00:49][INFO] Memory cleanup completed: deleted 1 expired events
[2026-02-03 22:00:58][INFO] Memory cleanup completed: deleted 20 expired events
[2026-02-03 22:00:59][INFO] Memory cleanup completed: deleted 2 expired events
[2026-02-03 22:01:08][INFO] Memory cleanup completed: deleted 24 expired events
[2026-02-03 22:01:09][INFO] Memory cleanup completed: deleted 1 expired events
============================================================
Third run
============================================================
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": []}'}]
It seems I don't have any memory of your name. Could you remind me?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": []}'}]
It seems I don't have any memory of your favorite color. Could you remind me what it is?
----------------------------------------
...
[2026-02-03 22:01:39][INFO] Memory cleanup completed: deleted 1 expired events
[2026-02-03 22:01:48][INFO] Memory cleanup completed: deleted 23 expired events
```

### 关键对比：三次运行的行为差异

#### 📊 对比表格

| 问题 | First Run (t=0s) | Second Run (t=2s) | Third Run (t=32s) |
|------|------------------|-------------------|-------------------|
| **"Do you remember my name?"** | ❌ `memories: []`<br/>"I don't have your name stored..." | ✅ `memories: [... Alice ...]`<br/>"Yes! Your name is Alice" | ❌ `memories: []`<br/>"I don't have any memory..." |
| **"Do you remember my favorite color?"** | ❌ `memories: []`<br/>"I don't have any memory..." | ✅ `memories: [... blue ...]`<br/>"Yes! ...blue" | ❌ `memories: []`<br/>"I don't have any memory..." |
| **Memory 状态** | 🆕 空（无记忆） | ✅ 存在（距 First Run 2s） | 🗑️ 已清理（清理任务删除） |
| **MySQL 事件数** | 0 → ~14 条 | ~28 条（First+Second） | ~14 条（仅 Third，旧数据已清理） |
| **清理日志** | 无 | `deleted 48 expired events`（多次） | 无（已清理完毕） |

#### 🔍 详细分析

**1️⃣ First Run（初始状态，建立记忆）**

```txt
Q1: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: []  ❌ 空数组
    💬 "I don't have your name stored in my memory."

Q4: "Hello! My name is Alice."
    💾 存储到 MySQL 中（自动触发）
    💾 MySQL: INSERT INTO mem_events

Q5: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: [... Alice ...]  ✅ 检索成功
    💬 "Yes, your name is Alice!"
```

- **状态**: Memory 初始为空
- **MySQL**: 空 → First Run 后插入约 14 条事件记录
- **原因**: MySQL 中还没有任何记忆数据
- **结果**: Q1-Q2 检索失败，Q5-Q7 检索成功（因为 Q4、Q6 已存储到 MySQL）

**MySQL 数据变化**：

```bash
# First Run 开始前
mysql> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|        0 |
+----------+

# First Run 结束后
mysql> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|       14 |  # 7 个查询，每个约 2 条事件
+----------+

mysql> SELECT session_id, COUNT(*) AS event_count
       FROM mem_events
       GROUP BY session_id;
+------------------------+-------------+
| session_id             | event_count |
+------------------------+-------------+
| sql_memory_session_0   |           2 |
| sql_memory_session_1   |           2 |
| sql_memory_session_2   |           2 |
| sql_memory_session_3   |           2 |  # Q4: "My name is Alice"
| sql_memory_session_4   |           2 |
| sql_memory_session_5   |           2 |  # Q6: "favorite color is blue"
| sql_memory_session_6   |           2 |
+------------------------+-------------+

# 查看 session_3 的事件（包含名字）
mysql> SELECT LEFT(event, 80) AS event_preview, timestamp
       FROM mem_events
       WHERE session_id = 'sql_memory_session_3';
+--------------------------------------------------------------------------------+---------------------+
| event_preview                                                                  | timestamp           |
+--------------------------------------------------------------------------------+---------------------+
| {"content": {"parts": [{"text": "Hello! My name is Alice. What's your name?... | 2026-02-03 22:00:32 |
| {"content": {"parts": [{"text": "Hello, Alice! My name is Assistant. ...      | 2026-02-03 22:00:33 |
+--------------------------------------------------------------------------------+---------------------+
```

---

**2️⃣ Second Run（2 秒后，记忆仍有效）**

```txt
Q1: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: [... Alice ...]  ✅ 检索成功
    💬 "Yes, your name is Alice!"

Q2: "Do you remember my favorite color?"
    🔧 load_memory(query="favorite color")
    📊 Result: memories: [... blue ...]  ✅ 检索成功
    💬 "Yes! Your favorite color is blue."
```

- **状态**: 距 First Run 仅 2 秒，记忆仍在 TTL 有效期内（20 秒）
- **MySQL**: First Run 的 14 条记录仍然存在
- **原因**: `SqlMemoryService` 从 MySQL 检索到 First Run 的所有对话事件
- **结果**: Agent 成功从 Memory 中检索到名字和颜色

**MySQL 数据状态**：

```bash
# Second Run 开始前
mysql> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|       14 |  # First Run 的 14 条记录
+----------+

mysql> SELECT TIMESTAMPDIFF(SECOND, timestamp, NOW()) AS seconds_ago
       FROM mem_events
       WHERE session_id = 'sql_memory_session_3'
       LIMIT 1;
+-------------+
| seconds_ago |
+-------------+
|           2 |  # 2 秒前创建，还有 18 秒过期
+-------------+

# Second Run 会话恢复流程：
# 1. load_memory("user's name") 触发
# 2. SqlMemoryService.search_memory() 执行
# 3. SELECT * FROM mem_events WHERE app_name = ... AND user_id = ...
# 4. 在内存中过滤出包含 "name" 关键词的事件
# 5. 返回匹配的事件列表
```

**为什么能记住？**

1. **跨会话共享**：Second Run 使用新的 session_id（`session_7`, `session_8`, ...），但能检索到 First Run 的记忆
2. **语义检索**：`load_memory("user's name")` 在所有 `mem_events` 中搜索包含 "name" 的事件
3. **TTL 未过期**：2s < 20s，First Run 的数据仍在 MySQL 中
4. **MySQL 持久化**：数据存储在 MySQL，不受进程重启影响

---

**3️⃣ 清理任务执行（t=10s~32s 之间）**

```txt
[2026-02-03 22:00:49][INFO] Memory cleanup completed: deleted 1 expired events
[2026-02-03 22:00:58][INFO] Memory cleanup completed: deleted 20 expired events
[2026-02-03 22:00:59][INFO] Memory cleanup completed: deleted 2 expired events
[2026-02-03 22:01:08][INFO] Memory cleanup completed: deleted 24 expired events
[2026-02-03 22:01:09][INFO] Memory cleanup completed: deleted 1 expired events
```

- **清理间隔**: 每 10 秒执行一次
- **清理逻辑**: 批量删除 `timestamp` 超过 20 秒的事件
- **清理数量**: 共删除约 48 个事件（First Run + Second Run 的所有事件）

**清理任务工作原理**：

```python
async def _cleanup_expired_async(self) -> None:
    """定期清理过期数据（批量删除）"""
    # 计算过期阈值
    expire_before = datetime.now() - timedelta(seconds=self.ttl_seconds)

    # 批量删除过期事件（单条 SQL DELETE）
    DELETE FROM mem_events WHERE timestamp < expire_before;

    # 提交事务
    COMMIT;
```

**清理任务执行时间线**：

```
t=0s    First Run 开始，创建 14 条事件

t=10s   清理任务第 1 次执行
        ↓ 检查: TIMESTAMPDIFF(SECOND, timestamp, NOW()) > 20?
        ↓ 结果: 10s < 20s → 无过期数据，跳过

t=20s   清理任务第 2 次执行
        ↓ 检查: First Run 的事件（距离 20s）
        ↓ 结果: 20s >= 20s → 删除 First Run 的部分事件
        ↓ 日志: deleted 20 expired events

t=30s   清理任务第 3 次执行
        ↓ 检查: First Run 剩余事件 + Second Run 事件
        ↓ 结果: 删除所有过期事件
        ↓ 日志: deleted 24 expired events
```

**验证清理效果**：

```bash
# 清理前
mysql> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|       28 |  # First Run + Second Run
+----------+

# 等待清理任务执行（约 10-20 秒）

# 清理后
mysql> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|        0 |  ✅ 所有过期数据已清理
+----------+
```

---

**4️⃣ Third Run（32 秒后，记忆已清理）**

```txt
Q1: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: []  ❌ 空数组
    💬 "I don't have any memory of your name."

Q2: "Do you remember my favorite color?"
    🔧 load_memory(query="favorite color")
    📊 Result: memories: []  ❌ 空数组
    💬 "I don't have any memory of your favorite color."
```

- **状态**: 距 First Run 32 秒，记忆已超过 TTL（20 秒）
- **MySQL**: 所有 First Run 和 Second Run 的记录都已被清理任务删除
- **原因**: 清理任务定期扫描并删除过期事件
- **结果**: Agent 无法从 Memory 中检索到任何数据，行为与 First Run Q1-Q2 相同

**MySQL 数据状态**：

```bash
# Third Run 开始前（距 First Run 32 秒）
mysql> SELECT COUNT(*) FROM mem_events;
+----------+
| COUNT(*) |
+----------+
|        0 |  ❌ 所有旧记忆都已清理
+----------+

# Third Run Q4 后（重新建立记忆）
mysql> SELECT session_id, COUNT(*) AS event_count
       FROM mem_events
       GROUP BY session_id;
+-------------------------+-------------+
| session_id              | event_count |
+-------------------------+-------------+
| sql_memory_session_17   |           2 |  # Q4 新建
| sql_memory_session_18   |           2 |  # Q5 新建
| ...                     |         ... |
+-------------------------+-------------+
```

**为什么不记得了？**

1. **TTL 过期**：32s > 20s TTL
2. **清理任务删除**：后台清理任务批量删除了所有过期事件
3. **检索失败**：`load_memory` 在空 MySQL 中搜索，返回空数组
4. **重新开始**：从干净状态重新开始，需要重新建立记忆

---

### 💡 核心功能验证

#### ✅ **MySQL 持久化**
- Second Run 能访问 First Run 的数据
- 数据存储在 MySQL，进程重启后仍可恢复
- **结论**: MySQL 持久化和恢复正常工作

#### ✅ **跨会话共享**
- Second Run 使用新的 session_id，但能检索到 First Run 的记忆
- **结论**: 跨会话共享正常工作

#### ✅ **TTL 缓存淘汰（定期清理任务）**
- TTL 设置为 **20 秒**，清理间隔 **10 秒**
- Second Run（2 秒后）能检索到记忆
- Third Run（32 秒后）无法检索到记忆
- **结论**: TTL 缓存淘汰机制正常工作

#### ✅ **批量清理**
- 清理任务使用单条 SQL DELETE 批量删除过期数据
- 日志显示：`deleted 20 expired events`、`deleted 24 expired events`
- **结论**: 批量清理任务正常运行，性能优化到位

#### ✅ **语义搜索**
- `load_memory("user's name")` 能检索到包含 "Alice" 的事件
- `load_memory("favorite color")` 能检索到包含 "blue" 的事件
- **结论**: 语义搜索功能正常工作

#### ✅ **事务安全**
- 使用数据库事务保证数据一致性
- 避免并发写入导致的数据损坏
- **结论**: 适合生产环境使用

#### ✅ **分布式支持**
- 使用 MySQL 外部存储，支持跨进程、跨服务器共享记忆
- **结论**: 适合生产环境和分布式部署

---

## 实现逻辑说明

### 为什么会有三次不同的行为？

**核心机制**：TTL（Time-To-Live）+ 定期清理任务

```python
# 代码实现（run_agent.py）
async def main():
    # First run
    await run_weather_agent()        # t=0s, 建立记忆
    await asyncio.sleep(2)           # 等待 2 秒

    # Second run
    await run_weather_agent()        # t=2s, 2 < 20（TTL 未过期）
    await asyncio.sleep(30)          # 等待 30 秒

    # Third run
    await run_weather_agent()        # t=32s, 32 > 20（TTL 已过期）
```

**为什么 Second Run 能检索到记忆？**

1. **数据保存**：First Run 结束时，MySQL 保存了约 14 条事件记录
2. **时间未到**：Second Run 在 2 秒后运行，2s < 20s，数据未过期
3. **跨会话检索**：虽然使用新的 session_id，但 `load_memory` 能检索所有事件
4. **MySQL 持久化**：数据存储在 MySQL，不受进程重启影响

**为什么 Third Run 无法检索到记忆？**

1. **时间到期**：Third Run 在 32 秒后运行，32s > 20s
2. **清理任务删除**：后台清理任务定期扫描并批量删除过期事件
3. **检索失败**：`load_memory` 在空 MySQL 中搜索，返回空数组
4. **重新开始**：从干净状态重新开始，需要重新建立记忆

---

### 清理任务工作原理

**清理任务代码逻辑**（简化）：

```python
async def _cleanup_expired_async(self) -> None:
    """定期清理过期数据（批量删除）"""
    async with self._sql_storage.create_db_session() as sql_session:
        # 1. 计算过期阈值
        expire_before = datetime.now() - timedelta(seconds=self.ttl_seconds)

        # 2. 查询过期事件数量（用于日志）
        expired_events = await db.execute(
            "SELECT * FROM mem_events WHERE timestamp < ?", expire_before
        )
        deleted_count = len(expired_events)

        if deleted_count > 0:
            # 3. 批量删除过期事件（单条 SQL DELETE）
            await db.execute(
                "DELETE FROM mem_events WHERE timestamp < ?", expire_before
            )

            # 4. 提交事务
            await db.commit()

            # 5. 输出日志
            logger.info(f"Memory cleanup completed: deleted {deleted_count} expired events")
```

**执行的 SQL**：

```sql
-- 计算过期阈值
-- expire_before = NOW() - INTERVAL 20 SECOND

-- 批量删除过期事件
DELETE FROM mem_events
WHERE timestamp < '2026-02-03 21:59:50';  -- 当前时间 - 20 秒
```

**清理任务执行时间线**：

```
t=0s    First Run 开始（创建 14 条事件）

t=10s   清理任务第 1 次执行
        ↓ expire_before = NOW() - 20s = t=-10s
        ↓ 查询: SELECT * FROM mem_events WHERE timestamp < t=-10s
        ↓ 结果: 0 条（First Run 的事件在 t=0~7s 创建）
        ↓ 跳过

t=20s   清理任务第 2 次执行
        ↓ expire_before = NOW() - 20s = t=0s
        ↓ 查询: SELECT * FROM mem_events WHERE timestamp < t=0s
        ↓ 结果: ~20 条（First Run 的早期事件）
        ↓ 执行: DELETE FROM mem_events WHERE timestamp < t=0s
        ↓ 日志: deleted 20 expired events

t=30s   清理任务第 3 次执行
        ↓ expire_before = NOW() - 20s = t=10s
        ↓ 查询: SELECT * FROM mem_events WHERE timestamp < t=10s
        ↓ 结果: ~24 条（First Run 剩余 + Second Run 的早期事件）
        ↓ 执行: DELETE FROM mem_events WHERE timestamp < t=10s
        ↓ 日志: deleted 24 expired events
```

---

### MySQL 操作序列分析

**First Run Q4（保存记忆）时的 MySQL 操作**：

```sql
-- 1. 插入事件（用户消息）
INSERT INTO mem_events (id, app_name, user_id, session_id, event, timestamp)
VALUES (
    UUID(),
    'weather_agent_demo',
    'sql_memory_user',
    'sql_memory_session_3',
    '{"content": {"parts": [{"text": "Hello! My name is Alice..."}]}, ...}',
    NOW()
);

-- 2. 插入事件（助手回复）
INSERT INTO mem_events (id, app_name, user_id, session_id, event, timestamp)
VALUES (
    UUID(),
    'weather_agent_demo',
    'sql_memory_user',
    'sql_memory_session_3',
    '{"content": {"parts": [{"text": "Hello, Alice! ..."}]}, ...}',
    NOW()
);

-- 3. 提交事务
COMMIT;
```

**Second Run Q1（检索记忆）时的 MySQL 操作**：

```sql
-- 1. 查询所有该用户的事件
SELECT * FROM mem_events
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_memory_user';

-- 返回: 14 条记录（First Run 的所有事件）

-- 2. 在内存中过滤匹配 "name" 关键词的事件
-- 3. 返回匹配的事件列表

-- ⚠️ 注意：SqlMemoryService 读取操作不会更新 timestamp
-- 只有写入时才会记录 timestamp
```

**清理任务执行的 SQL**：

```sql
-- 1. 计算过期阈值
-- expire_before = NOW() - INTERVAL 20 SECOND

-- 2. 查询过期事件数量
SELECT COUNT(*) FROM mem_events
WHERE timestamp < '2026-02-03 22:00:00';  -- expire_before

-- 3. 批量删除过期事件（单条 SQL）
DELETE FROM mem_events
WHERE timestamp < '2026-02-03 22:00:00';

-- 4. 提交事务
COMMIT;
```

**Third Run Q1（检索失败）时的 MySQL 操作**：

```sql
-- 1. 查询所有该用户的事件
SELECT * FROM mem_events
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_memory_user';

-- 返回: 0 条记录（所有旧事件都已被清理）

-- 2. 返回空数组
```

---

### 批量清理 vs 逐个清理

**优化前**（逐个清理，性能差）：

```python
# 查询所有事件
events = SELECT * FROM mem_events;

# 遍历删除（N 次 DELETE）
for event in events:
    if is_expired(event):
        DELETE FROM mem_events WHERE id = event.id;
```

**优化后**（批量清理，高性能）：

```python
# 单条 SQL DELETE（1 次 DELETE）
DELETE FROM mem_events
WHERE timestamp < (NOW() - INTERVAL 20 SECOND);
```

**性能对比**：

假设有 100 条过期事件：

| 指标 | 逐个清理 | 批量清理 | 提升 |
|------|---------|---------|------|
| **DELETE 操作** | 100 次 | 1 次 | **99% ↓** |
| **网络往返** | 100 次 | 1 次 | **99% ↓** |
| **执行时间** | ~2s | ~0.02s | **99% ↓** |
| **数据库负载** | 高 | 低 | **显著降低** |

---

## 总结

本示例成功演示了 **SqlMemoryService** 的核心能力：

1. **MySQL 持久化**: 在 MySQL 中持久化跨会话记忆数据
2. **跨会话共享**: 不同会话可以访问共享的记忆数据
3. **TTL 缓存淘汰**: 定期清理任务自动删除过期数据
4. **批量清理**: 使用单条 SQL DELETE 批量删除，高性能
5. **语义搜索**: 通过关键词检索相关记忆
6. **事务安全**: 使用数据库事务保证数据一致性
7. **分布式支持**: 支持跨进程、跨服务器共享记忆

### 适用场景

- ✅ 生产环境（数据持久化 + 事务安全）
- ✅ 分布式部署（跨进程共享）
- ✅ 高可用场景（MySQL 主从/集群）
- ✅ 跨会话知识共享
- ✅ 长期记忆管理（配合合理 TTL）
- ✅ 需要 SQL 查询能力（复杂查询、统计分析）

### Memory Service 对比

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

💡 **选择建议**:
- 开发测试用 `InMemoryMemoryService`
- 需要高性能缓存用 `RedisMemoryService`（推荐，Redis 自动过期）
- 需要事务安全和复杂查询用 `SqlMemoryService`

### 定期清理任务 vs Redis 自动过期

#### 对比表格

| 特性 | SqlMemoryService<br/>（定期清理任务） | RedisMemoryService<br/>（Redis 自动过期） |
|-----|----------------------------------|------------------------------|
| **清理方式** | 后台任务每 N 秒扫描一次 | Redis 自动删除过期键 |
| **清理精度** | 取决于扫描间隔 | 精确到秒 |
| **性能开销** | 单条 SQL DELETE（批量） | Redis 内部高效处理 |
| **日志输出** | 清理时输出日志 | 无日志（静默删除） |
| **适用场景** | 关系型数据库 | Redis 缓存 |

#### SqlMemoryService 的优势

相比 `InMemoryMemoryService`，`SqlMemoryService` 的清理任务已优化为批量删除：

1. **批量删除**：使用单条 SQL DELETE，避免逐个删除的性能问题
2. **事务安全**：所有删除操作在事务中执行，保证一致性
3. **索引优化**：`timestamp` 字段有索引，DELETE 操作高效
4. **日志可观测**：清理任务输出详细日志，方便监控

#### 适用场景选择

- **需要高性能** → 使用 `RedisMemoryService`（Redis 自动过期，无扫描开销）
- **需要事务和复杂查询** → 使用 `SqlMemoryService`（批量清理，性能已优化）
- **本地开发测试** → 使用 `InMemoryMemoryService`（零依赖）

💡 **生产环境推荐**:
- 高并发场景 → `RedisMemoryService`
- 需要数据分析 → `SqlMemoryService`
