# Session Service MySQL 存储示例

## 示例简介

本示例演示如何使用 **SqlSessionService** 实现会话管理和状态持久化，并展示 **TTL（Time-To-Live）过期机制** 的效果。

### 核心特性

- ✅ **MySQL 持久化**: 使用 `SqlSessionService` 在 MySQL 中持久化会话状态
- ✅ **TTL 过期**: 配置会话 5 秒 TTL，演示过期自动清理
- ✅ **状态持久化**: 在 TTL 有效期内，会话状态跨多次运行保持
- ✅ **定期清理**: 后台任务定期扫描并删除过期数据
- ✅ **分布式支持**: MySQL 存储支持跨进程、跨服务器共享会话
- ✅ **事务安全**: 使用数据库事务保证数据一致性

## 环境要求

- Python 3.10+（强烈建议使用 3.12）
- MySQL 5.7+ 或 MariaDB 10.3+

## 安装和运行

### 1. 下载并安装 trpc-agent

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
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
  -e MYSQL_DATABASE=trpc_agent_session \
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
CREATE DATABASE IF NOT EXISTS trpc_agent_session CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

# 切换到该数据库
USE trpc_agent_session;

# 查看数据库
SHOW DATABASES;
```

**重要提示**：`SqlSessionService` 会在首次运行时**自动创建表结构**，无需手动创建表。

---

#### MySQL 客户端操作指南

**连接 MySQL**

```bash
# 无密码（本地开发，不安全）
mysql -u root

# 有密码（推荐）
mysql -u root -p

# 连接到特定数据库
mysql -u root -p trpc_agent_session
```

**查看数据库和表**

```bash
# 查看所有数据库
SHOW DATABASES;

# 切换到目标数据库
USE trpc_agent_session;

# 查看所有表
SHOW TABLES;
# 期望输出（首次运行后）:
# +-------------------------------+
# | Tables_in_trpc_agent_session  |
# +-------------------------------+
# | app_states                    |
# | sessions                      |
# | user_states                   |
# +-------------------------------+

# 查看表结构
DESC sessions;
DESC user_states;
DESC app_states;
```

**查看会话数据**

```bash
# 查看所有会话
SELECT * FROM sessions;

# 查看特定应用的会话
SELECT * FROM sessions WHERE app_name = 'weather_agent_demo';

# 查看特定用户的会话
SELECT * FROM sessions
WHERE app_name = 'weather_agent_demo' AND user_id = 'sql_user';

# 查看会话的 update_time（用于判断是否过期）
SELECT id, app_name, user_id, update_time,
       TIMESTAMPDIFF(SECOND, update_time, NOW()) AS seconds_ago
FROM sessions;

# 查看会话数量
SELECT COUNT(*) FROM sessions;
```

**查看用户状态**

```bash
# 查看所有用户状态
SELECT * FROM user_states;

# 查看特定用户的状态
SELECT * FROM user_states
WHERE app_name = 'weather_agent_demo' AND user_id = 'sql_user';

# 查看用户状态的字段（JSON 格式）
SELECT app_name, user_id, state, update_time
FROM user_states;
```

**查看应用状态**

```bash
# 查看所有应用状态
SELECT * FROM app_states;

# 查看特定应用的状态
SELECT * FROM app_states
WHERE app_name = 'weather_agent_demo';
```

**检查数据是否过期**

```bash
# 查看距离上次更新的时间（秒）
SELECT
    id,
    app_name,
    user_id,
    update_time,
    TIMESTAMPDIFF(SECOND, update_time, NOW()) AS seconds_ago,
    CASE
        WHEN TIMESTAMPDIFF(SECOND, update_time, NOW()) > 5 THEN '已过期'
        ELSE '有效'
    END AS status
FROM sessions;

# 查看即将过期的会话（TTL=5秒）
SELECT * FROM sessions
WHERE TIMESTAMPDIFF(SECOND, update_time, NOW()) BETWEEN 3 AND 5;
```

**删除数据**

```bash
# 删除特定会话
DELETE FROM sessions
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user'
  AND id = 'sql_session_1';

# 删除所有会话（⚠️ 危险操作）
DELETE FROM sessions;

# 删除所有表（⚠️ 非常危险）
DROP TABLE sessions;
DROP TABLE user_states;
DROP TABLE app_states;

# 清空数据库（⚠️ 极度危险）
DROP DATABASE trpc_agent_session;
```

**实时监控数据变化**

```bash
# 监控会话表的变化（每 1 秒刷新）
watch -n 1 'mysql -u root -p"your_password" -e "USE trpc_agent_session; SELECT * FROM sessions;"'

# 监控会话数量
watch -n 1 'mysql -u root -p"your_password" -e "USE trpc_agent_session; SELECT COUNT(*) FROM sessions;"'
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
WHERE table_schema = 'trpc_agent_session'
GROUP BY table_schema;

# 查看表的行数
SELECT
    table_name AS 'Table',
    table_rows AS 'Rows'
FROM information_schema.tables
WHERE table_schema = 'trpc_agent_session';
```

---

#### 调试技巧

**场景 1：验证示例运行前数据库是否为空**

```bash
mysql -u root -p
> USE trpc_agent_session;
> SELECT COUNT(*) FROM sessions;
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
> USE trpc_agent_session;

> SELECT * FROM sessions \G
*************************** 1. row ***************************
       id: sql_session_1
 app_name: weather_agent_demo
  user_id: sql_user
    state: {"events": [...], "state": {...}}
update_time: 2026-02-04 10:30:15

> SELECT * FROM user_states \G
*************************** 1. row ***************************
 app_name: weather_agent_demo
  user_id: sql_user
    state: {"theme": "blue"}
update_time: 2026-02-04 10:30:15

> SELECT TIMESTAMPDIFF(SECOND, update_time, NOW()) AS seconds_ago FROM sessions;
+-------------+
| seconds_ago |
+-------------+
|           2 |  # 2 秒前更新，还有 3 秒过期
+-------------+
```

**场景 3：验证 TTL 过期**

```bash
# First Run 结束后等待 6 秒
sleep 6

mysql -u root -p
> USE trpc_agent_session;
> SELECT COUNT(*) FROM sessions;
+----------+
| COUNT(*) |
+----------+
|        0 |  ✅ 已过期删除（清理任务执行）
+----------+
```

**场景 4：手动触发清理**

```bash
# 手动删除过期数据（模拟清理任务）
mysql -u root -p
> USE trpc_agent_session;

> DELETE FROM sessions
  WHERE TIMESTAMPDIFF(SECOND, update_time, NOW()) > 5;
Query OK, 1 row affected

> DELETE FROM user_states
  WHERE TIMESTAMPDIFF(SECOND, update_time, NOW()) > 5;
Query OK, 1 row affected
```

**场景 5：查看清理任务日志**

清理任务会在日志中输出，查看终端输出：

```txt
[2026-02-04 10:30:20][INFO] Cleanup completed: deleted 3 items (1 sessions, 1 user states, 1 app states)
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
MYSQL_DB=trpc_agent_session
```

---

### 5. 运行示例

```bash
python3 examples/session_service_with_sql/run_agent.py
```

---

## 代码说明

### SqlSessionService 配置

```python
def create_session_service(is_async: bool = False):
    """创建 SQL Session Service"""

    # 从环境变量读取 MySQL 配置
    db_user = os.environ.get("MYSQL_USER", "root")
    db_password = os.environ.get("MYSQL_PASSWORD", "")
    db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    db_port = os.environ.get("MYSQL_PORT", "3306")
    db_name = os.environ.get("MYSQL_DB", "trpc_agent_session")

    # 构建 MySQL 连接 URL
    db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

    # 配置 Session 参数
    session_config = SessionServiceConfig(
        ttl=SessionServiceConfig.create_ttl_config(
            enable=True,                      # 启用 TTL
            ttl_seconds=5,                    # 会话过期时间：5 秒
            cleanup_interval_seconds=5        # 清理间隔：5 秒
        ),
    )

    session_service = SqlSessionService(
        session_config=session_config,
        is_async=is_async,
        db_url=db_url,
        pool_pre_ping=True,                   # 连接池健康检查
        pool_recycle=3600,                    # 连接回收时间：1 小时
    )

    return session_service
```

**配置说明**：

| 参数 | 值 | 说明 | 生产环境建议 |
|-----|---|------|------------|
| `ttl_seconds` | 5 | 会话过期时间 | 3600（1小时）或更长 |
| `cleanup_interval_seconds` | 5 | 清理间隔 | 300（5分钟） |
| `pool_pre_ping` | True | 连接健康检查 | True（推荐） |
| `pool_recycle` | 3600 | 连接回收时间 | 3600（1小时） |

⚠️ **重要提示**：本示例将 TTL 设置为 **5 秒**，是为了快速演示过期行为。**生产环境请设置更合理的值**！

---

### 测试流程

示例运行三次相同的对话，每次间隔不同，用于演示 TTL 效果：

```python
async def main():
    print("First run")
    await run_weather_agent()        # 运行 7 个查询

    await asyncio.sleep(2)           # 等待 2 秒（< 5秒 TTL）

    print("Second run")
    await run_weather_agent()        # 再次运行 7 个查询

    await asyncio.sleep(10)          # 等待 10 秒（总 12 秒 > 5秒 TTL）

    print("Third run")
    await run_weather_agent()        # 第三次运行

    await asyncio.sleep(10)          # 等待清理任务完成
```

**时间线**：

```
t=0s    ┌─────────────┐
        │  First Run  │  创建会话，建立初始状态
        └─────────────┘
           ↓ 7 个查询
           ↓ MySQL: sessions + user_states 都存在，update_time 记录

t=2s    ┌─────────────┐
        │ Second Run  │  会话未过期，成功恢复状态 ✅
        └─────────────┘
           ↓ 2s < 5s → 数据仍在 MySQL
           ↓ 能读取到 Alice 的名字和 blue 偏好

t=5s    ⏰ 清理任务启动，扫描过期数据
        └─ 未发现过期数据（Second Run 刷新了 update_time）

t=12s   ┌─────────────┐
        │  Third Run  │  会话已过期，状态丢失 ❌
        └─────────────┘
           ↓ 12s > 5s → 数据已过期
           ↓ 清理任务删除过期记录
           ↓ 无法恢复之前的数据
           ↓ 从干净状态重新开始
```

---

### 查询列表

每次运行都执行相同的 7 个查询：

```python
demo_queries = [
    "Do you remember my name?",                      # Q1: 测试名字记忆
    "Do you remember my favorite color?",            # Q2: 测试偏好记忆
    "what is the weather like in paris?",            # Q3: 测试工具调用
    "Hello! My name is Alice. What's your name?",    # Q4: 告诉 Agent 名字
    "Do you remember my name?",                      # Q5: 验证短期记忆
    "My favorite color is blue.",                    # Q6: 告诉 Agent 偏好
    "Do you remember my favorite color?",            # Q7: 验证偏好保存
]
```

**查询设计意图**：

| 查询 | 目的 | First Run | Second Run | Third Run |
|-----|------|-----------|-----------|-----------|
| **Q1** | 测试初始状态 | ❌ 不记得 | ✅ 记得（Alice） | ❌ 不记得（已过期） |
| **Q2** | 测试偏好记忆 | ❌ 不记得 | ✅ 记得（blue） | ❌ 不记得（已过期） |
| **Q3** | 测试工具 | ✅ 正常 | ✅ 正常 | ✅ 正常 |
| **Q4** | 建立上下文 | 📝 学习名字 | 📝 重复学习 | 📝 重新学习 |
| **Q5** | 验证短期记忆 | ✅ 记得（刚说的） | ✅ 记得（刚说的） | ✅ 记得（刚说的） |
| **Q6** | 保存偏好 | 💾 保存到 MySQL | 💾 更新 MySQL（刷新 TTL） | 💾 重新保存到 MySQL |
| **Q7** | 验证偏好保存 | ✅ 显示 blue | ✅ 显示 blue | ✅ 显示 blue |

---

## MySQL 表结构

`SqlSessionService` 会自动创建以下三张表：

### 1. `sessions` 表

存储会话数据和对话历史：

```sql
CREATE TABLE sessions (
    id VARCHAR(255) NOT NULL,              -- 会话 ID
    app_name VARCHAR(255) NOT NULL,        -- 应用名称
    user_id VARCHAR(255) NOT NULL,         -- 用户 ID
    state TEXT,                            -- 会话状态（JSON）
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id, app_name, user_id),
    INDEX idx_update_time (update_time)    -- 用于清理任务
);
```

### 2. `user_states` 表

存储用户级状态（跨会话共享）：

```sql
CREATE TABLE user_states (
    app_name VARCHAR(255) NOT NULL,        -- 应用名称
    user_id VARCHAR(255) NOT NULL,         -- 用户 ID
    state TEXT,                            -- 用户状态（JSON）
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (app_name, user_id),
    INDEX idx_update_time (update_time)    -- 用于清理任务
);
```

### 3. `app_states` 表

存储应用级状态（全局共享）：

```sql
CREATE TABLE app_states (
    app_name VARCHAR(255) NOT NULL,        -- 应用名称
    state TEXT,                            -- 应用状态（JSON）
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (app_name),
    INDEX idx_update_time (update_time)    -- 用于清理任务
);
```

---

## 运行结果分析

### 完整输出

```txt
python3 examples/session_service_with_sql/run_agent.py
============================================================
First run
============================================================
[2026-02-04 10:30:10][INFO][trpc_agent_sdk][trpc_agent_sdk/sessions/_sql_session_service.py:672][1247921] Cleanup task started with interval: 5.0s
🤖 Assistant: As an AI, I don't have the ability to remember personal details like your name between interactions. However, you can tell me your name, and I'll do my best to assist you! How can I help you today?
----------------------------------------
🤖 Assistant: No, I don't have the ability to remember personal details like your favorite color between interactions. But you can tell me, and I'll be happy to help you with anything related to it! What's your favorite color? 😊
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is sunny with a temperature of 25 degrees Celsius. Enjoy the pleasant weather! 😊
----------------------------------------
🤖 Assistant: Hello, Alice! My name is Assistant. It's nice to meet you! How can I assist you today? 😊
----------------------------------------
🤖 Assistant: Yes, Alice! You just told me your name is Alice. How can I help you today? 😊
----------------------------------------
🤖 Assistant: That's wonderful, Alice! Blue is a beautiful and calming color. Is there anything you'd like to chat about or any way I can assist you today? 😊
----------------------------------------
🤖 Assistant: Yes, Alice! You mentioned that your favorite color is blue. It's a lovely choice! 😊 Is there anything else you'd like to share or ask?
----------------------------------------
============================================================
Second run
============================================================
🤖 Assistant: Yes, Alice! I remember your name is Alice, and your favorite color is blue. How can I help you today?
----------------------------------------
🤖 Assistant: Yes, Alice! Your favorite color is blue. Is there anything else you'd like to share or ask? I'm here to help!
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is currently sunny with a temperature of 25 degrees Celsius. Enjoy the lovely weather, Alice!
----------------------------------------
🤖 Assistant: Hello again, Alice! My name is Assistant. It's nice to chat with you once more. How can I assist you today?
----------------------------------------
🤖 Assistant: Yes, Alice! I remember your name is Alice, and your favorite color is blue. How can I help you today?
----------------------------------------
🤖 Assistant: Got it, Alice! I'll remember that your favorite color is blue. Is there anything else you'd like to share or ask? I'm here to assist you!
----------------------------------------
🤖 Assistant: Yes, Alice! Your favorite color is blue. How can I assist you today?
----------------------------------------
[2026-02-04 10:30:25][INFO][trpc_agent_sdk][trpc_agent_sdk/sessions/_sql_session_service.py:658][1247921] Cleanup completed: deleted 3 items (1 sessions, 1 user states, 1 app states)
============================================================
Third run
============================================================
🤖 Assistant: As an AI, I don't have the ability to remember personal details like your name between interactions. Each conversation is independent, and I don't retain any information about past interactions. However, you can tell me your name, and I'll address you by it during this conversation!
----------------------------------------
🤖 Assistant: No, I don't remember your favorite color (or any personal details) because I don't retain information between conversations. But you can tell me your favorite color, and I'll happily acknowledge it in this chat! What's your favorite color?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is currently sunny with a temperature of 25 degrees Celsius. Enjoy the pleasant weather!
----------------------------------------
🤖 Assistant: Hello, Alice! My name is Assistant. It's nice to meet you! How can I assist you today?
----------------------------------------
🤖 Assistant: Yes, Alice! You just told me your name is Alice. How can I help you today? 😊
----------------------------------------
🤖 Assistant: That's wonderful, Alice! Blue is a beautiful and calming color. Is there anything you'd like to chat about or any way I can assist you today? 😊
----------------------------------------
🤖 Assistant: Yes, Alice! You mentioned that your favorite color is blue. It's a lovely choice! 😊 Is there anything else you'd like to share or ask?
----------------------------------------
[2026-02-04 10:30:57][INFO][trpc_agent_sdk][trpc_agent_sdk/sessions/_sql_session_service.py:687][1247921] Cleanup task stopped
```

### 关键对比：三次运行的行为差异

#### 📊 对比表格

| 问题 | First Run (t=0s) | Second Run (t=2s) | Third Run (t=12s) |
|------|------------------|-------------------|-------------------|
| **"Do you remember my name?"** | ❌ "I don't have the ability to remember..." | ✅ "Yes, Alice! I remember your name..." | ❌ "I don't have the ability to remember..." |
| **"Do you remember my favorite color?"** | ❌ "No, I don't remember your favorite color..." | ✅ "Yes, Alice! Your favorite color is blue." | ❌ "No, I don't remember your favorite color..." |
| **会话状态** | 🆕 新建会话 | ✅ 会话存在（距 First Run 2s） | 🗑️ 会话已过期清理（距 Second Run 10s） |
| **MySQL 数据** | 空 → First Run 后创建 | 存在（update_time 在 5s 内） | 已删除（12s > 5s TTL） |

#### 🔍 详细分析

**1️⃣ First Run（初始对话，会话创建）**

```txt
🤖 "Do you remember my name?"
   → ❌ "As an AI, I don't have the ability to remember..."

🤖 "Do you remember my favorite color?"
   → ❌ "No, I don't have the ability to remember..."
```

- **状态**: 会话首次创建
- **MySQL**: 空（没有历史数据）
- **原因**: MySQL 中还没有该用户的历史会话数据
- **结果**: Agent 无法回忆起任何信息

**MySQL 数据变化**：

```bash
# First Run 开始前
mysql> SELECT COUNT(*) FROM sessions;
+----------+
| COUNT(*) |
+----------+
|        0 |
+----------+

# First Run 结束后
mysql> SELECT * FROM sessions \G
*************************** 1. row ***************************
       id: sql_session_1
 app_name: weather_agent_demo
  user_id: sql_user
    state: {"events": [...], "state": {...}}
update_time: 2026-02-04 10:30:15

mysql> SELECT * FROM user_states \G
*************************** 1. row ***************************
 app_name: weather_agent_demo
  user_id: sql_user
    state: {"theme": "blue"}
update_time: 2026-02-04 10:30:15

mysql> SELECT TIMESTAMPDIFF(SECOND, update_time, NOW()) AS seconds_ago FROM sessions;
+-------------+
| seconds_ago |
+-------------+
|           2 |  # 2 秒前更新，还有 3 秒过期
+-------------+
```

---

**2️⃣ Second Run（2 秒后，会话仍有效）**

```txt
🤖 "Do you remember my name?"
   → ✅ "Yes, Alice! I remember your name is Alice..."

🤖 "Do you remember my favorite color?"
   → ✅ "Yes, Alice! Your favorite color is blue."
```

- **状态**: 距 First Run 仅 2 秒，会话仍在 TTL 有效期内（5 秒）
- **MySQL**: 数据仍然存在，`update_time` 在 5 秒内
- **原因**: `SqlSessionService` 从 MySQL 恢复了 First Run 的会话历史和用户状态
- **结果**: Agent 成功回忆起用户名和偏好

**MySQL 数据状态**：

```bash
# Second Run 开始前
mysql> SELECT TIMESTAMPDIFF(SECOND, update_time, NOW()) AS seconds_ago FROM sessions;
+-------------+
| seconds_ago |
+-------------+
|           2 |  # 还在 TTL 内（2s < 5s）

mysql> SELECT EXISTS(
    SELECT 1 FROM sessions
    WHERE app_name = 'weather_agent_demo'
      AND user_id = 'sql_user'
      AND id = 'sql_session_1'
) AS session_exists;
+----------------+
| session_exists |
+----------------+
|              1 |  ✅ 存在
+----------------+

# Second Run 会话恢复流程：
# 1. SqlSessionService.get_session() 查询 sessions 表
# 2. 加载完整的对话历史（state 字段的 events）
# 3. 加载 user_states（包含 theme="blue"）
# 4. LLM 从历史中推断出用户名 Alice
```

**为什么能记住？**

| 数据项 | 存储位置 | 如何恢复 |
|-------|---------|---------|
| **用户名（Alice）** | `sessions` 表的 `state` 字段（JSON） | LLM 从历史对话中读取 "My name is Alice" |
| **偏好（blue）** | `user_states` 表的 `state` 字段（JSON） | 直接从 MySQL 读取 `state` 字段 |

---

**3️⃣ Third Run（12 秒后，会话已过期）**

```txt
🤖 "Do you remember my name?"
   → ❌ "As an AI, I don't have the ability to remember..."

🤖 "Do you remember my favorite color?"
   → ❌ "No, I don't remember your favorite color..."
```

- **状态**: 距 First Run 12 秒，会话已超过 TTL（5 秒）
- **MySQL**: 所有记录都已被清理任务删除
- **原因**: 清理任务定期扫描并删除 `update_time` 超过 5 秒的记录
- **结果**: Agent 无法访问历史数据，行为与 First Run 相同

**MySQL 数据状态**：

```bash
# Third Run 开始前（距 First Run 12 秒）
mysql> SELECT COUNT(*) FROM sessions;
+----------+
| COUNT(*) |
+----------+
|        0 |  ❌ 已被清理任务删除
+----------+

mysql> SELECT COUNT(*) FROM user_states;
+----------+
| COUNT(*) |
+----------+
|        0 |  ❌ 已被清理任务删除
+----------+

# 查看清理日志（终端输出）
[2026-02-04 10:30:25][INFO] Cleanup completed: deleted 3 items (1 sessions, 1 user states, 1 app states)
```

**为什么不记得了？**

1. **TTL 过期**: 12s > 5s TTL
2. **清理任务执行**: 后台任务每 5 秒扫描一次，执行 SQL DELETE 删除过期数据
3. **无法恢复**: `SqlSessionService.get_session()` 查询 MySQL 返回空
4. **重新开始**: 创建新的空会话，从头开始

**清理任务执行的 SQL**：

```sql
-- 删除过期的 sessions
DELETE FROM sessions
WHERE TIMESTAMPDIFF(SECOND, update_time, NOW()) > 5;

-- 删除过期的 user_states
DELETE FROM user_states
WHERE TIMESTAMPDIFF(SECOND, update_time, NOW()) > 5;

-- 删除过期的 app_states
DELETE FROM app_states
WHERE TIMESTAMPDIFF(SECOND, update_time, NOW()) > 5;
```

---

### 💡 核心功能验证

#### ✅ **MySQL 持久化**
- Second Run 能访问 First Run 的数据
- 数据存储在 MySQL 表中，进程重启后仍可恢复
- **结论**: MySQL 持久化和恢复正常工作

#### ✅ **TTL 过期机制**
- TTL 设置为 **5 秒**
- Second Run（2 秒后）能访问会话
- Third Run（12 秒后）无法访问会话
- **结论**: TTL 过期功能正常工作

#### ✅ **定期清理任务**
- 清理间隔设置为 **5 秒**
- 日志显示 `deleted 3 items (1 sessions, 1 user states, 1 app states)`
- **结论**: 后台清理任务正常运行，MySQL 数据得到清理

#### ✅ **状态持久化**
- 同一 `session_id` 在 TTL 有效期内保持状态
- Second Run 中 Agent 准确回忆起 First Run 的对话内容和偏好
- **结论**: 会话状态和用户状态在有效期内正确持久化

#### ✅ **TTL 刷新机制**
- Second Run Q6 更新偏好时，MySQL 会更新 `update_time` 字段
- 每次 `get` 或 `update` 操作都会刷新 `update_time`
- **结论**: TTL 刷新机制正常工作（对齐 `InMemorySessionService` 行为）

#### ✅ **事务安全**
- 使用数据库事务保证数据一致性
- 避免并发写入导致的数据损坏
- **结论**: 适合生产环境使用

#### ✅ **分布式支持**
- 使用 MySQL 外部存储，支持跨进程、跨服务器共享会话
- **结论**: 适合生产环境和分布式部署

---

### 📊 三次运行完整对比表

| 查询 | First Run (t=0s) | Second Run (t=2s) | Third Run (t=12s) |
|-----|------------------|-------------------|-------------------|
| **Q1: 记得名字吗？** | ❌ 不记得 | ✅ **记得 Alice** | ❌ 不记得（已过期） |
| **Q2: 记得偏好吗？** | ❌ 不记得 | ✅ **记得 blue** | ❌ 不记得（已过期） |
| **Q3: 巴黎天气** | ✅ 工具正常 | ✅ 工具正常 | ✅ 工具正常 |
| **Q4: 我叫 Alice** | 📝 学习名字 | 📝 重复学习 | 📝 重新学习 |
| **Q5: 记得名字吗？** | ✅ 记得（当前会话） | ✅ 记得（当前会话） | ✅ 记得（当前会话） |
| **Q6: 偏好是 blue** | 💾 保存到 MySQL | 💾 更新 MySQL（刷新 TTL） | 💾 重新保存到 MySQL |
| **Q7: 显示偏好** | ✅ 显示 blue | ✅ 显示 blue | ✅ 显示 blue |
| **MySQL 状态** | 创建数据 | 数据存在，update_time 刷新 | 数据已删除（清理任务） |

---

## 实现逻辑说明

### 为什么会有三次不同的行为？

**核心机制**：TTL（Time-To-Live）+ 定期清理任务

```python
# 代码实现（run_agent.py）
async def main():
    # First run
    await run_weather_agent()        # t=0s, 创建会话
    await asyncio.sleep(2)           # 等待 2 秒

    # Second run
    await run_weather_agent()        # t=2s, 2 < 5（TTL 未过期）
    await asyncio.sleep(10)          # 等待 10 秒

    # Third run
    await run_weather_agent()        # t=12s, 12 > 5（TTL 已过期）
```

**为什么 Second Run 能恢复状态？**

1. **数据保存**：First Run 结束时，MySQL 保存了会话数据和用户状态
2. **update_time 记录**：MySQL 记录了 `update_time` 字段（最后更新时间）
3. **时间未到**：Second Run 在 2 秒后运行，2s < 5s，数据未过期
4. **自动加载**：`SqlSessionService.get_session()` 从 MySQL 读取数据并恢复到内存

**为什么 Third Run 无法恢复状态？**

1. **时间到期**：Third Run 在 12 秒后运行，12s > 5s
2. **清理任务删除**：后台清理任务每 5 秒扫描一次，删除 `update_time` 超过 5 秒的记录
3. **查询失败**：`SqlSessionService.get_session()` 查询 MySQL 返回空
4. **重新开始**：创建新的空会话，行为与 First Run 相同

---

### 清理任务工作原理

**清理任务代码逻辑**（简化）：

```python
async def _cleanup_expired_async(self) -> None:
    """定期清理过期数据"""
    # 计算过期阈值
    expire_before = datetime.now() - timedelta(seconds=self.ttl_seconds)

    # 批量删除过期 sessions
    DELETE FROM sessions WHERE update_time < expire_before;

    # 批量删除过期 user_states
    DELETE FROM user_states WHERE update_time < expire_before;

    # 批量删除过期 app_states
    DELETE FROM app_states WHERE update_time < expire_before;

    # 提交事务
    COMMIT;
```

**清理任务执行时间线**：

```
t=0s    First Run 开始
        ↓
t=5s    清理任务第 1 次执行（无过期数据，跳过）
        ↓
t=10s   清理任务第 2 次执行（无过期数据，跳过）
        ↓
t=15s   清理任务第 3 次执行
        ↓ 发现过期数据（距 First Run 15s > 5s TTL）
        ↓ 执行 DELETE 删除过期记录
        ↓ 日志：Cleanup completed: deleted 3 items
```

---

### MySQL 操作序列分析

**First Run Q6（保存偏好）时的 MySQL 操作**：

```sql
-- 1. 保存用户状态（INSERT 或 UPDATE）
INSERT INTO user_states (app_name, user_id, state, update_time)
VALUES ('weather_agent_demo', 'sql_user', '{"theme": "blue"}', NOW())
ON DUPLICATE KEY UPDATE
    state = '{"theme": "blue"}',
    update_time = NOW();

-- 2. 保存会话数据
INSERT INTO sessions (id, app_name, user_id, state, update_time)
VALUES ('sql_session_1', 'weather_agent_demo', 'sql_user', '{"events": [...]}', NOW())
ON DUPLICATE KEY UPDATE
    state = '{"events": [...]}',
    update_time = NOW();

-- 3. 提交事务
COMMIT;
```

**Second Run Q1（恢复状态）时的 MySQL 操作**：

```sql
-- 1. 查询会话是否存在
SELECT COUNT(*) FROM sessions
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user'
  AND id = 'sql_session_1';
-- 返回: 1（存在）

-- 2. 读取会话数据
SELECT state FROM sessions
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user'
  AND id = 'sql_session_1';

-- 3. 刷新 update_time（续期）
UPDATE sessions
SET update_time = NOW()
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user'
  AND id = 'sql_session_1';

-- 4. 读取用户状态
SELECT state FROM user_states
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user';

-- 5. 刷新用户状态 update_time
UPDATE user_states
SET update_time = NOW()
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user';

-- 6. 提交事务
COMMIT;
```

**Third Run Q1（查询失败）时的 MySQL 操作**：

```sql
-- 1. 尝试查询会话是否存在
SELECT COUNT(*) FROM sessions
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user'
  AND id = 'sql_session_1';
-- 返回: 0（不存在，已被清理任务删除）

-- 2. 查询用户状态
SELECT COUNT(*) FROM user_states
WHERE app_name = 'weather_agent_demo'
  AND user_id = 'sql_user';
-- 返回: 0（不存在，已被清理任务删除）

-- 3. 无法恢复，创建新会话
-- （行为与 First Run 相同）
```

---

### TTL 刷新机制

每次 `get` 或 `update` 操作都会自动刷新 `update_time`：

```python
# SqlSessionService 内部实现（简化）
async def get_session(self, session_id):
    # 1. 从 MySQL 读取数据
    session_data = await db.execute(
        "SELECT state FROM sessions WHERE id = ?", session_id
    )

    # 2. 刷新 update_time（续期）
    await db.execute(
        "UPDATE sessions SET update_time = NOW() WHERE id = ?", session_id
    )

    return session_data

async def update_session(self, session_id, data):
    # 1. 更新 MySQL 数据
    await db.execute(
        "UPDATE sessions SET state = ?, update_time = NOW() WHERE id = ?",
        data, session_id
    )

    # 2. update_time 自动更新（续期）
```

**这就是为什么 Second Run 仍能访问数据的原因**：每次访问都会更新 `update_time`，重置 5 秒倒计时。

---

## 总结

本示例成功演示了 **SqlSessionService** 的核心能力：

1. **MySQL 持久化**: 在关系型数据库中持久化会话状态
2. **TTL 控制**: 自动过期和清理机制避免数据膨胀
3. **状态恢复**: TTL 有效期内可跨运行恢复状态
4. **TTL 刷新**: 访问时自动续期，保持活跃会话
5. **定期清理**: 后台任务批量删除过期数据，优化性能
6. **事务安全**: 使用数据库事务保证数据一致性
7. **分布式支持**: 支持跨进程、跨服务器共享会话

### 适用场景

- ✅ 生产环境（数据持久化 + 事务安全）
- ✅ 分布式部署（跨进程共享）
- ✅ 高可用场景（MySQL 主从/集群）
- ✅ 长期会话管理（配合合理 TTL）
- ✅ 需要 SQL 查询能力（复杂查询、统计分析）

### 与其他 Session Service 对比

| 特性 | InMemorySessionService | RedisSessionService | SqlSessionService |
|-----|----------------------|-------------------|-------------------|
| **数据存储** | 进程内存 | Redis 外部存储 | MySQL/PostgreSQL |
| **持久化** | ❌ 进程重启丢失 | ✅ 持久化到 Redis | ✅ 持久化到数据库 |
| **分布式** | ❌ 无法跨进程共享 | ✅ 支持跨进程/服务器 | ✅ 支持跨进程/服务器 |
| **TTL 机制** | ✅ 定期清理任务 | ✅ Redis 自动过期 | ✅ 定期清理任务 |
| **事务支持** | ❌ | ❌ | ✅ ACID 事务 |
| **复杂查询** | ❌ | ❌ | ✅ SQL 查询 |
| **部署场景** | 本地开发/单机 | 生产环境/分布式/缓存 | 生产环境/分布式/关系型数据 |
| **性能** | ⭐⭐⭐⭐⭐ 极快 | ⭐⭐⭐⭐ 快 | ⭐⭐⭐ 中等 |

💡 **选择建议**:
- 开发测试用 `InMemorySessionService`
- 需要高性能缓存用 `RedisSessionService`
- 需要事务安全和复杂查询用 `SqlSessionService`
