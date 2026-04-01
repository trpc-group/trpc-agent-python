# Session Service Redis 存储示例

## 示例简介

本示例演示如何使用 **RedisSessionService** 实现会话管理和状态持久化，并展示 **TTL（Time-To-Live）过期机制** 的效果。

### 核心特性

- ✅ **Redis 持久化**: 使用 `RedisSessionService` 在 Redis 中持久化会话状态
- ✅ **TTL 过期**: 配置会话 5 秒 TTL，演示过期自动清理
- ✅ **状态持久化**: 在 TTL 有效期内，会话状态跨多次运行保持
- ✅ **多层状态**: 支持 `session_state`（会话级）、`user_state`（用户级）、`app_state`（应用级）
- ✅ **分布式支持**: Redis 存储支持跨进程、跨服务器共享会话

---

## 🏗️ 项目结构

```
examples/redis_session_service/
├── .env                    # 环境配置（LLM、Redis连接）
├── run_agent.py           # 主运行脚本（核心示例代码）
├── agent/
│   ├── __init__.py
│   ├── agent.py           # Agent 定义（LlmAgent + 工具）
│   ├── config.py          # 配置读取（从 .env 读取）
│   ├── prompts.py         # Agent 指令（system prompt）
│   └── tools.py           # Agent 工具函数
└── README.md              # 本文档
```

---

## 🔧 环境要求

- **Python 版本**: 3.10+（强烈建议使用 3.12）
- **Redis 服务**: 需要运行中的 Redis 实例（支持 Redis < 6.0 和 Redis 6.0+ ACL）

---

## 🚀 快速开始

### 1. 安装 trpc-agent-python

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip3 install -e .
```

### 2. 准备 Redis 环境

#### 启动 Redis 服务

选择以下任一方式启动 Redis：

```bash
# 方式1：使用系统服务
service redis start

# 方式2：直接启动 Redis（默认端口 6379）
redis-server

# 方式3：使用 Docker（推荐）
docker run -d -p 6379:6379 --name redis redis:latest
```

验证 Redis 是否启动成功：

```bash
redis-cli ping
# 输出: PONG  ✅ 启动成功
```

---

#### Redis 客户端操作指南

**连接 Redis**

```bash
# 无密码（本地开发）
redis-cli

# 有密码（认证后才能操作）
redis-cli
> AUTH your_password
OK
```

**查看数据**

```bash
# 查看所有键
KEYS *

# 查看特定模式的键（更精确）
KEYS session:*                    # 所有会话
KEYS user_state:*                 # 所有用户状态
KEYS app_state:*                  # 所有应用状态
KEYS *weather_agent_demo*         # 特定应用的所有键

# 查看键的数量
DBSIZE

# 查看键的类型
TYPE session:weather_agent_demo:redis_user:redis_session_1
# 输出: hash

# 查看哈希表的所有字段和值
HGETALL session:weather_agent_demo:redis_user:redis_session_1
# 输出:
# 1) "events"
# 2) "[{...}]"    # JSON 格式的事件列表
# 3) "state"
# 4) "{...}"      # JSON 格式的状态

# 查看哈希表的特定字段
HGET user_state:weather_agent_demo:redis_user theme
# 输出: "blue"

# 查看所有字段名
HKEYS session:weather_agent_demo:redis_user:redis_session_1
# 输出:
# 1) "events"
# 2) "state"
```

**查看 TTL（剩余过期时间）**

```bash
# 查看键的剩余生存时间（秒）
TTL session:weather_agent_demo:redis_user:redis_session_1
# 输出: 4         # 还有 4 秒过期
# 输出: -1        # 永不过期
# 输出: -2        # 键不存在或已过期

# 实时监控 TTL 变化
watch -n 1 'redis-cli TTL session:weather_agent_demo:redis_user:redis_session_1'
```

**检查键是否存在**

```bash
EXISTS session:weather_agent_demo:redis_user:redis_session_1
# 输出: 1  ✅ 存在
# 输出: 0  ❌ 不存在
```

**删除数据**

```bash
# 删除特定键
DEL session:weather_agent_demo:redis_user:redis_session_1

# 删除匹配模式的所有键（⚠️ 危险操作）
redis-cli KEYS "session:weather_agent_demo:*" | xargs redis-cli DEL

# 清空当前数据库的所有键（⚠️ 非常危险）
FLUSHDB

# 清空所有数据库的所有键（⚠️ 极度危险）
FLUSHALL
```

**实时监控 Redis 操作**

```bash
# 监控所有 Redis 命令（调试时非常有用）
redis-cli MONITOR

# 期望输出（示例运行时）:
# 1738597234.123456 [0 127.0.0.1:12345] "HGETALL" "session:weather_agent_demo:redis_user:redis_session_1"
# 1738597234.234567 [0 127.0.0.1:12345] "EXPIRE" "session:weather_agent_demo:redis_user:redis_session_1" "5"
# 1738597234.345678 [0 127.0.0.1:12345] "HSET" "user_state:weather_agent_demo:redis_user" "theme" "blue"
```

**查看 Redis 信息**

```bash
# 查看 Redis 服务器信息
INFO

# 查看内存使用情况
INFO memory

# 查看键空间统计
INFO keyspace
# 输出:
# db0:keys=3,expires=3,avg_ttl=4521
```

---

#### 调试技巧

**场景 1：验证示例运行前 Redis 是否为空**

```bash
redis-cli
> AUTH test
> KEYS *
(empty array)  ✅ Redis 为空，可以开始测试
```

**场景 2：查看 First Run 后的数据**

```bash
# First Run 结束后立即查询
redis-cli
> KEYS *
1) "session:weather_agent_demo:redis_user:redis_session_1"
2) "user_state:weather_agent_demo:redis_user"

> TTL session:weather_agent_demo:redis_user:redis_session_1
(integer) 4  # 还有 4 秒过期

> HGET user_state:weather_agent_demo:redis_user theme
"blue"
```

**场景 3：验证 TTL 过期**

```bash
# First Run 结束后等待 6 秒
sleep 6

redis-cli
> EXISTS session:weather_agent_demo:redis_user:redis_session_1
(integer) 0  ✅ 已过期删除

> KEYS *
(empty array)  ✅ 所有数据都已清空
```

**场景 4：手动模拟会话恢复**

```bash
# 手动设置用户状态（模拟已存在的偏好）
redis-cli
> HSET user_state:weather_agent_demo:redis_user theme "dark_mode"
> EXPIRE user_state:weather_agent_demo:redis_user 60
> HGETALL user_state:weather_agent_demo:redis_user
1) "theme"
2) "dark_mode"

# 然后运行示例，Agent 会读取到这个偏好
```

### 3. 配置环境变量

编辑 `.env` 文件，设置 LLM 和 Redis 配置：

```bash
# LLM 配置（必填）
TRPC_AGENT_API_KEY=your_api_key
TRPC_AGENT_BASE_URL=http://v2.open.venus.woa.com/llmproxy
TRPC_AGENT_MODEL_NAME=deepseek-v3-local-II

# Redis 配置
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0

# Redis 认证配置（根据你的 Redis 版本选择）
# 选项1：无密码（本地开发）
# 留空即可

# 选项2：Redis < 6.0（仅密码认证）
REDIS_PASSWORD=your_password

# 选项3：Redis 6.0+（ACL 用户名+密码）
# REDIS_USER=your_username
# REDIS_PASSWORD=your_password
```

**Redis 认证说明**：

| Redis 版本 | 认证方式 | 配置方式 |
|-----------|---------|---------|
| 本地无密码 | 无需认证 | 留空 `REDIS_PASSWORD` |
| Redis < 6.0 | 仅密码 | 设置 `REDIS_PASSWORD`，留空 `REDIS_USER` |
| Redis 6.0+ ACL | 用户名+密码 | 同时设置 `REDIS_USER` 和 `REDIS_PASSWORD` |

### 4. 运行示例

```bash
cd examples/redis_session_service/
python3 run_agent.py
```

---

## 代码说明

### RedisSessionService 配置

```python
session_config = SessionServiceConfig(
    max_events=1000,
    event_ttl_seconds=5,
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,                      # 启用 TTL
        ttl_seconds=5,                    # 会话过期时间：5 秒
        cleanup_interval_seconds=5        # 清理间隔：5 秒（Redis 自动过期，此参数实际不生效）
    ),
)

# 构建 Redis URL
if db_password:
    if db_user:
        db_url = f"redis://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    else:
        db_url = f"redis://:{db_password}@{db_host}:{db_port}/{db_name}"
else:
    db_url = f"redis://{db_host}:{db_port}/{db_name}"

session_service = RedisSessionService(
    is_async=False,
    db_url=db_url,
    session_config=session_config,
)
```

**配置说明**：

| 参数 | 值 | 说明 | 生产环境建议 |
|-----|---|------|------------|
| `ttl_seconds` | 5 | 会话过期时间 | 3600（1小时）或更长 |
| `event_ttl_seconds` | 5 | 事件过期时间 | 与 ttl_seconds 相同 |
| `max_events` | 1000 | 最大事件数 | 根据需求调整 |

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
```

**时间线**：

```
t=0s    ┌─────────────┐
        │  First Run  │  创建会话，建立初始状态
        └─────────────┘
           ↓ 7 个查询
           ↓ Redis: session + user_state 都存在，TTL=5s

t=2s    ┌─────────────┐
        │ Second Run  │  会话未过期，成功恢复状态 ✅
        └─────────────┘
           ↓ 2s < 5s → 数据仍在 Redis
           ↓ 能读取到 Alice 的名字和 blue 偏好

t=12s   ┌─────────────┐
        │  Third Run  │  会话已过期，状态丢失 ❌
        └─────────────┘
           ↓ 12s > 5s → Redis 已自动删除过期键
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
| **Q6** | 保存偏好 | 💾 保存到 Redis | 💾 更新 Redis（刷新 TTL） | 💾 重新保存到 Redis |
| **Q7** | 验证偏好保存 | ✅ 显示 blue | ✅ 显示 blue | ✅ 显示 blue |

---

## Redis 数据结构

运行示例时，Redis 中会创建以下键：

```bash
# 会话数据（包含会话历史和事件）
session:weather_agent_demo:redis_user:redis_session_1

# 用户级状态（跨会话共享，如偏好设置）
user_state:weather_agent_demo:redis_user

# 应用级状态（全局共享，本示例未使用）
app_state:weather_agent_demo
```

**查看 Redis 数据示例**：

```bash
redis-cli
> KEYS *
1) "session:weather_agent_demo:redis_user:redis_session_1"
2) "user_state:weather_agent_demo:redis_user"

> HGETALL user_state:weather_agent_demo:redis_user
1) "theme"
2) "blue"

> TTL user_state:weather_agent_demo:redis_user
(integer) 4  # 还有 4 秒过期
```

---

## 运行结果分析

### 完整输出

```txt
python3 examples/session_service_with_redis/run_agent.py
============================================================
First run
============================================================
🤖 Assistant: As an AI, I don't have the ability to remember personal details like your name between interactions. However, you can tell me your name, and I'll do my best to assist you! How can I help you today?
----------------------------------------
🤖 Assistant: I don’t have the ability to remember personal details like your favorite color between interactions. But you can tell me, and I’ll be happy to help you with anything related to it! What’s your favorite color? 😊
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is currently sunny with a temperature of 25 degrees Celsius. Enjoy the pleasant weather if you're there! 😊
----------------------------------------
🤖 Assistant: Hello, Alice! My name is Assistant. It's nice to meet you! How can I assist you today? 😊
----------------------------------------
🤖 Assistant: Yes, Alice! You just told me your name is Alice. 😊 How can I assist you further?
----------------------------------------
🤖 Assistant: Got it, Alice! Your favorite color is blue—such a lovely choice! 💙 Is there anything blue-related or anything else I can help you with today? 😊
----------------------------------------
🤖 Assistant: Yes, Alice! You told me your favorite color is blue. 💙 Is there something blue-related you'd like to explore or any other way I can assist you? 😊
----------------------------------------
============================================================
Second run
============================================================
🤖 Assistant: Yes, Alice! Your name is Alice, and your favorite color is blue. 💙 Is there anything else you'd like to share or ask? 😊
----------------------------------------
🤖 Assistant: Yes, Alice! Your favorite color is **blue**. 💙 Is there something specific you'd like to know or do related to your favorite color? 😊
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is currently sunny with a temperature of 25 degrees Celsius. Enjoy the sunshine! 😊
----------------------------------------
🤖 Assistant: Hello, Alice! My name is [assistant]. It's nice to meet you! How can I assist you today? 😊
----------------------------------------
🤖 Assistant: Yes, of course! Your name is **Alice**. 😊 How can I help you today, Alice?
----------------------------------------
🤖 Assistant: That's a great choice, Alice! Blue is such a calming and beautiful color. 😊 Is there anything else you'd like to share or ask? I'm here to help!
----------------------------------------
🤖 Assistant: Of course, Alice! Your favorite color is **blue**. 😊 It's a lovely choice! Is there anything else you'd like to talk about or need help with?
----------------------------------------
============================================================
Third run
============================================================
🤖 Assistant: As an AI, I don't have the ability to remember personal details like your name unless you share it with me in this conversation. How can I assist you today?
----------------------------------------
🤖 Assistant: I don’t have the ability to remember personal details like your favorite color unless you share it with me in this conversation. Feel free to tell me, and I’ll do my best to assist you! 😊
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is sunny with a temperature of 25 degrees Celsius. Enjoy the pleasant weather! 😊
----------------------------------------
🤖 Assistant: Hello, Alice! My name is Assistant. It's nice to meet you! How can I assist you today? 😊
----------------------------------------
🤖 Assistant: Yes, Alice! You mentioned your name earlier. How can I assist you today? 😊
----------------------------------------
🤖 Assistant: Got it, Alice! Your favorite color is blue. I'll keep that in mind for our conversation. Let me know how I can assist you further! 😊💙
----------------------------------------
🤖 Assistant: Of course, Alice! Your favorite color is **blue**. 💙 Let me know if there's anything else you'd like to share or ask! 😊
----------------------------------------
```

### 关键对比：三次运行的行为差异

#### 📊 对比表格

| 问题 | First Run (t=0s) | Second Run (t=2s) | Third Run (t=12s) |
|------|------------------|-------------------|-------------------|
| **"Do you remember my name?"** | ❌ "I don't have the ability to remember..." | ✅ "Yes, your name is Alice!" | ❌ "I don't have the ability to remember..." |
| **"Do you remember my favorite color?"** | ❌ "I don't remember your favorite color..." | ✅ "Yes! Your favorite color is blue." | ❌ "I don't remember your favorite color..." |
| **会话状态** | 🆕 新建会话 | ✅ 会话存在（距 First Run 2s） | 🗑️ 会话已过期清理（距 Second Run 10s） |
| **Redis 数据** | 空 → First Run 后创建 | 存在（TTL 剩余 3s） | 已删除（12s > 5s TTL） |

#### 🔍 详细分析

**1️⃣ First Run（初始对话，会话创建）**

```txt
🤖 "Do you remember my name?"
   → ❌ "As an AI, I don't have the ability to remember..."

🤖 "Do you remember my favorite color?"
   → ❌ "I don't have the ability to remember..."
```

- **状态**: 会话首次创建
- **Redis**: 空（没有历史数据）
- **原因**: Redis 中还没有该用户的历史会话数据
- **结果**: Agent 无法回忆起任何信息

**Redis 数据变化**：

```bash
# First Run 开始前
redis-cli> KEYS *
(empty array)

# First Run 结束后
redis-cli> KEYS *
1) "session:weather_agent_demo:redis_user:redis_session_1"
2) "user_state:weather_agent_demo:redis_user"

redis-cli> HGET user_state:weather_agent_demo:redis_user theme
"blue"

redis-cli> TTL user_state:weather_agent_demo:redis_user
(integer) 4  # 还有 4 秒过期
```

---

**2️⃣ Second Run（2 秒后，会话仍有效）**

```txt
🤖 "Do you remember my name?"
   → ✅ "Yes, Alice! Your name is Alice..."

🤖 "Do you remember my favorite color?"
   → ✅ "Yes! Your favorite color is blue."
```

- **状态**: 距 First Run 仅 2 秒，会话仍在 TTL 有效期内（5 秒）
- **Redis**: 数据仍然存在，TTL 还剩约 3 秒
- **原因**: `RedisSessionService` 从 Redis 恢复了 First Run 的会话历史和用户状态
- **结果**: Agent 成功回忆起用户名和偏好

**Redis 数据状态**：

```bash
# Second Run 开始前
redis-cli> TTL session:weather_agent_demo:redis_user:redis_session_1
(integer) 3  # 还有 3 秒

redis-cli> EXISTS user_state:weather_agent_demo:redis_user
(integer) 1  ✅ 存在

# Second Run 会话恢复流程：
# 1. RedisSessionService.get_session() 读取 session 键
# 2. 加载完整的对话历史（events）
# 3. 加载 user_state（包含 theme="blue"）
# 4. LLM 从历史中推断出用户名 Alice
```

**为什么能记住？**

| 数据项 | 存储位置 | 如何恢复 |
|-------|---------|---------|
| **用户名（Alice）** | `session` 的事件历史 | LLM 从历史对话中读取 "My name is Alice" |
| **偏好（blue）** | `user_state` 的 `theme` 字段 | 直接从 Redis 读取 `HGET user_state:... theme` |

---

**3️⃣ Third Run（12 秒后，会话已过期）**

```txt
🤖 "Do you remember my name?"
   → ❌ "As an AI, I don't have the ability to remember..."

🤖 "Do you remember my favorite color?"
   → ❌ "No, I don't remember your favorite color..."
```

- **状态**: 距 First Run 12 秒，会话已超过 TTL（5 秒）并被 Redis 自动删除
- **Redis**: 所有键都已过期删除
- **原因**: Redis 的 TTL 机制自动清理过期数据
- **结果**: Agent 无法访问历史数据，行为与 First Run 相同

**Redis 数据状态**：

```bash
# Third Run 开始前（距 First Run 12 秒）
redis-cli> KEYS *
(empty array)  ❌ 所有数据都已过期删除

redis-cli> EXISTS session:weather_agent_demo:redis_user:redis_session_1
(integer) 0  ❌ 不存在

redis-cli> EXISTS user_state:weather_agent_demo:redis_user
(integer) 0  ❌ 不存在
```

**为什么不记得了？**

1. **TTL 过期**: 12s > 5s TTL
2. **Redis 自动删除**: Redis 的 EXPIRE 机制自动清理过期键
3. **无法恢复**: `RedisSessionService.get_session()` 查询 Redis 返回空
4. **重新开始**: 创建新的空会话，从头开始

---

### 💡 核心功能验证

#### ✅ **Redis 持久化**
- Second Run 能访问 First Run 的数据
- **结论**: Redis 持久化和恢复正常工作

#### ✅ **TTL 过期机制**
- TTL 设置为 **5 秒**
- Second Run（2 秒后）能访问会话
- Third Run（12 秒后）无法访问会话
- **结论**: TTL 过期功能正常工作，Redis 自动清理过期键

#### ✅ **状态持久化**
- 同一 `session_id` 在 TTL 有效期内保持状态
- Second Run 中 Agent 准确回忆起 First Run 的对话内容和偏好
- **结论**: 会话状态和用户状态在有效期内正确持久化

#### ✅ **TTL 刷新机制**
- Second Run Q6 更新偏好时，Redis 会执行 `EXPIRE` 命令刷新 TTL
- 每次 `get` 或 `update` 操作都会续期
- **结论**: TTL 刷新机制正常工作（对齐 `InMemorySessionService` 行为）

#### ✅ **分布式支持**
- 使用 Redis 外部存储，支持跨进程、跨服务器共享会话
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
| **Q6: 偏好是 blue** | 💾 保存到 Redis | 💾 更新 Redis（刷新 TTL） | 💾 重新保存到 Redis |
| **Q7: 显示偏好** | ✅ 显示 blue | ✅ 显示 blue | ✅ 显示 blue |
| **Redis 状态** | 创建数据，TTL=5s | 数据存在，TTL 剩余 3s | 数据已删除（过期）

---

## 实现逻辑说明

### 为什么会有三次不同的行为？

**核心机制**：TTL（Time-To-Live）+ Redis 自动过期

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

1. **数据保存**：First Run 结束时，Redis 保存了会话数据和用户状态
2. **TTL 设置**：Redis 对每个键设置 `EXPIRE 5` 秒
3. **时间未到**：Second Run 在 2 秒后运行，2s < 5s，数据仍然存在
4. **自动加载**：`RedisSessionService.get_session()` 从 Redis 读取数据并恢复到内存

**为什么 Third Run 无法恢复状态？**

1. **时间到期**：Third Run 在 12 秒后运行，12s > 5s
2. **Redis 删除**：Redis 的 TTL 机制在 5 秒后自动删除了所有过期键
3. **查询失败**：`RedisSessionService.get_session()` 查询 Redis 返回空
4. **重新开始**：创建新的空会话，行为与 First Run 相同

---

### Redis 命令序列分析

**First Run Q6（保存偏好）时的 Redis 操作**：

```bash
# 1. 保存用户状态
HSET user_state:weather_agent_demo:redis_user theme "blue"

# 2. 设置过期时间
EXPIRE user_state:weather_agent_demo:redis_user 5

# 3. 保存会话数据
HSET session:weather_agent_demo:redis_user:redis_session_1 events "[...]"
HSET session:weather_agent_demo:redis_user:redis_session_1 state "{...}"

# 4. 设置会话过期时间
EXPIRE session:weather_agent_demo:redis_user:redis_session_1 5
```

**Second Run Q1（恢复状态）时的 Redis 操作**：

```bash
# 1. 检查会话是否存在
EXISTS session:weather_agent_demo:redis_user:redis_session_1
# 返回: 1（存在）

# 2. 读取会话数据
HGETALL session:weather_agent_demo:redis_user:redis_session_1
# 返回: events, state 等字段

# 3. 刷新 TTL（续期）
EXPIRE session:weather_agent_demo:redis_user:redis_session_1 5

# 4. 读取用户状态
HGETALL user_state:weather_agent_demo:redis_user
# 返回: theme="blue"

# 5. 刷新用户状态 TTL
EXPIRE user_state:weather_agent_demo:redis_user 5
```

**Third Run Q1（查询失败）时的 Redis 操作**：

```bash
# 1. 尝试检查会话是否存在
EXISTS session:weather_agent_demo:redis_user:redis_session_1
# 返回: 0（不存在，已过期删除）

# 2. 查询用户状态
EXISTS user_state:weather_agent_demo:redis_user
# 返回: 0（不存在，已过期删除）

# 3. 无法恢复，创建新会话
# （行为与 First Run 相同）
```

---

### TTL 刷新机制

每次 `get` 或 `update` 操作都会自动刷新 TTL：

```python
# RedisSessionService 内部实现（简化）
async def get_session(self, session_id):
    # 1. 从 Redis 读取数据
    session_data = await redis.hgetall(f"session:{session_id}")

    # 2. 刷新 TTL（续期）
    await redis.expire(f"session:{session_id}", self.ttl_seconds)

    return session_data

async def update_session(self, session_id, data):
    # 1. 更新 Redis 数据
    await redis.hset(f"session:{session_id}", mapping=data)

    # 2. 刷新 TTL（续期）
    await redis.expire(f"session:{session_id}", self.ttl_seconds)
```

**这就是为什么 Second Run 仍能访问数据的原因**：每次访问都会重置 5 秒倒计时。

---

## 总结

本示例成功演示了 **RedisSessionService** 的核心能力：

1. **Redis 持久化**: 在外部存储中持久化会话状态
2. **TTL 控制**: 自动过期和清理机制避免内存泄漏
3. **状态恢复**: TTL 有效期内可跨运行恢复状态
4. **TTL 刷新**: 访问时自动续期，保持活跃会话
5. **分布式支持**: 支持跨进程、跨服务器共享会话

### 适用场景

- ✅ 生产环境（数据持久化）
- ✅ 分布式部署（跨进程共享）
- ✅ 高可用场景（Redis 集群）
- ✅ 长期会话管理（配合合理 TTL）

### 与 InMemorySessionService 对比

| 特性 | InMemorySessionService | RedisSessionService |
|-----|----------------------|-------------------|
| **数据存储** | 进程内存 | Redis 外部存储 |
| **持久化** | ❌ 进程重启丢失 | ✅ 持久化到 Redis |
| **分布式** | ❌ 无法跨进程共享 | ✅ 支持跨进程/服务器 |
| **TTL 机制** | ✅ 定期清理任务 | ✅ Redis 自动过期 |
| **部署场景** | 本地开发/单机 | 生产环境/分布式 |

💡 **选择建议**: 开发测试用 `InMemorySessionService`，生产环境用 `RedisSessionService`。
