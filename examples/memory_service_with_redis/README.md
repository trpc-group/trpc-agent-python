# Memory Service Redis 存储示例

## 示例简介

本示例演示如何使用 **RedisMemoryService** 实现跨会话的记忆管理，并展示 **TTL（Time-To-Live）缓存淘汰机制** 的效果。

### 核心特性

- ✅ **Redis 持久化**: 使用 `RedisMemoryService` 在 Redis 中持久化跨会话记忆数据
- ✅ **跨会话共享**: 不同会话（session）可以共享同一份记忆数据
- ✅ **TTL 缓存淘汰**: 配置记忆 10 秒 TTL，演示 Redis 自动过期清理
- ✅ **语义搜索**: 通过 `load_memory` 工具根据查询关键词检索相关记忆
- ✅ **自动清理**: Redis 的 EXPIRE 机制自动清理过期键，无需后台任务
- ✅ **分布式支持**: Redis 存储支持跨进程、跨服务器共享记忆

## 环境要求

- Python 3.10+（强烈建议使用 3.12）
- Redis 服务

## 安装和运行

### 1. 下载并安装 trpc-agent

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

---

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

**查看 Memory 数据**

```bash
# 查看所有键
KEYS *

# 查看特定模式的键（Memory 使用 List 存储）
KEYS memory:*

# 查看特定应用和用户的记忆键
KEYS memory:weather_agent_demo/redis_memory_user:*

# 查看键的数量
DBSIZE

# 查看键的类型（Memory 使用 List）
TYPE memory:weather_agent_demo/redis_memory_user:redis_memory_session_0
# 输出: list

# 查看 List 的长度（事件数量）
LLEN memory:weather_agent_demo/redis_memory_user:redis_memory_session_0
# 输出: 2  # 有 2 个事件

# 查看 List 的所有元素（事件列表）
LRANGE memory:weather_agent_demo/redis_memory_user:redis_memory_session_0 0 -1
# 输出:
# 1) "{\"content\": {\"parts\": [{\"text\": \"Do you remember my name?\"}]}, ...}"
# 2) "{\"content\": {\"parts\": [{\"text\": \"It seems I don't have your name...\"}]}, ...}"

# 查看 List 的第一个元素
LINDEX memory:weather_agent_demo/redis_memory_user:redis_memory_session_0 0

# 查看 List 的最后一个元素
LINDEX memory:weather_agent_demo/redis_memory_user:redis_memory_session_0 -1
```

**查看 TTL（剩余过期时间）**

```bash
# 查看键的剩余生存时间（秒）
TTL memory:weather_agent_demo/redis_memory_user:redis_memory_session_0
# 输出: 8         # 还有 8 秒过期
# 输出: -1        # 永不过期
# 输出: -2        # 键不存在或已过期

# 实时监控 TTL 变化
watch -n 1 'redis-cli TTL memory:weather_agent_demo/redis_memory_user:redis_memory_session_0'

# 查看所有 memory 键的 TTL
redis-cli KEYS "memory:*" | while read key; do
    echo "$key: $(redis-cli TTL $key)s"
done
```

**检查键是否存在**

```bash
EXISTS memory:weather_agent_demo/redis_memory_user:redis_memory_session_0
# 输出: 1  ✅ 存在
# 输出: 0  ❌ 不存在
```

**删除数据**

```bash
# 删除特定键
DEL memory:weather_agent_demo/redis_memory_user:redis_memory_session_0

# 删除匹配模式的所有键（⚠️ 危险操作）
redis-cli KEYS "memory:weather_agent_demo/*" | xargs redis-cli DEL

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
# 1738597234.123456 [0 127.0.0.1:12345] "RPUSH" "memory:weather_agent_demo/redis_memory_user:redis_memory_session_0" "{...}"
# 1738597234.234567 [0 127.0.0.1:12345] "EXPIRE" "memory:weather_agent_demo/redis_memory_user:redis_memory_session_0" "10"
# 1738597234.345678 [0 127.0.0.1:12345] "LRANGE" "memory:weather_agent_demo/redis_memory_user:redis_memory_session_3" "0" "-1"
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
# db0:keys=7,expires=7,avg_ttl=8521
```

---

#### 调试技巧

**场景 1：验证示例运行前 Redis 是否为空**

```bash
redis-cli
> KEYS *
(empty array)  ✅ Redis 为空，可以开始测试
```

**场景 2：查看 First Run 后的数据**

```bash
# First Run 结束后立即查询
redis-cli
> KEYS memory:*
1) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_0"
2) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_1"
3) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_2"
4) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_3"
5) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_4"
6) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_5"
7) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_6"

> TTL memory:weather_agent_demo/redis_memory_user:redis_memory_session_3
(integer) 8  # 还有 8 秒过期

> LLEN memory:weather_agent_demo/redis_memory_user:redis_memory_session_3
(integer) 2  # 有 2 个事件

> LRANGE memory:weather_agent_demo/redis_memory_user:redis_memory_session_3 0 -1
1) "{\"content\": {\"parts\": [{\"text\": \"Hello! My name is Alice...\"}]}, ...}"
2) "{\"content\": {\"parts\": [{\"text\": \"Hello, Alice! ...\"}]}, ...}"
```

**场景 3：验证 TTL 过期（Redis 自动删除）**

```bash
# First Run 结束后等待 11 秒
sleep 11

redis-cli
> EXISTS memory:weather_agent_demo/redis_memory_user:redis_memory_session_0
(integer) 0  ✅ 已过期删除

> KEYS memory:*
(empty array)  ✅ 所有数据都已被 Redis 自动清空
```

**场景 4：手动设置记忆数据**

```bash
# 手动创建记忆（模拟已存在的数据）
redis-cli
> RPUSH memory:weather_agent_demo/redis_memory_user:test_session "{\"content\": {\"parts\": [{\"text\": \"My name is Bob\"}]}}"
> EXPIRE memory:weather_agent_demo/redis_memory_user:test_session 60
> LRANGE memory:weather_agent_demo/redis_memory_user:test_session 0 -1
1) "{\"content\": {\"parts\": [{\"text\": \"My name is Bob\"}]}}"
```

---

### 3. 配置环境变量

在 `.env` 文件中设置 LLM 和 Redis 配置：

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
# REDIS_PASSWORD=your_password

# 选项3：Redis 6.0+（ACL 用户名+密码）
# REDIS_USER=your_username
# REDIS_PASSWORD=your_password
```

---

### 4. 运行示例

```bash
python3 examples/memory_service_with_redis/run_agent.py
```

---

## 代码说明

### RedisMemoryService 配置

```python
def create_memory_service(is_async: bool = False):
    """创建 Redis Memory Service"""

    # 从环境变量读取 Redis 配置
    db_user = os.environ.get("REDIS_USER", "")
    db_password = os.environ.get("REDIS_PASSWORD", "")
    db_host = os.environ.get("REDIS_HOST", "127.0.0.1")
    db_port = os.environ.get("REDIS_PORT", "6379")
    db_name = os.environ.get("REDIS_DB", "0")

    # 构建 Redis 连接 URL（支持多种认证方式）
    if db_password:
        if db_user:
            db_url = f"redis://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        else:
            db_url = f"redis://:{db_password}@{db_host}:{db_port}/{db_name}"
    else:
        db_url = f"redis://{db_host}:{db_port}/{db_name}"

    # 配置 Memory 参数
    memory_service_config = MemoryServiceConfig(
        enabled=True,                      # 启用 Memory 功能
        ttl=MemoryServiceConfig.create_ttl_config(
            enable=True,                   # 启用 TTL
            ttl_seconds=10,                # 记忆过期时间：10 秒
            cleanup_interval_seconds=10    # 清理间隔：10 秒（Redis 自动过期，此参数不生效）
        ),
    )

    memory_service = RedisMemoryService(
        is_async=is_async,
        db_url=db_url,
        memory_service_config=memory_service_config,
    )

    return memory_service
```

**配置说明**：

| 参数 | 值 | 说明 | 生产环境建议 |
|-----|---|------|------------|
| `enabled` | True | 启用 Memory 功能 | True |
| `ttl_seconds` | 10 | 记忆过期时间 | 86400（24小时）或更长 |
| `cleanup_interval_seconds` | 10 | ⚠️ Redis 不需要此参数 | N/A |

⚠️ **重要提示**：
- 本示例将 TTL 设置为 **10 秒**，是为了快速演示缓存淘汰行为
- **Redis 使用 EXPIRE 命令自动过期**，不需要后台清理任务
- **生产环境请设置更合理的值**（如 24 小时或更长）

---

### 测试流程

示例运行三次相同的对话，每次间隔不同，用于演示 TTL 缓存淘汰效果：

```python
async def main():
    print("First run")
    await run_weather_agent()        # 运行 7 个查询

    await asyncio.sleep(2)           # 等待 2 秒（< 10秒 TTL）

    print("Second run")
    await run_weather_agent()        # 再次运行 7 个查询

    await asyncio.sleep(30)          # 等待 30 秒（> 10秒 TTL）

    print("Third run")
    await run_weather_agent()        # 第三次运行
```

**时间线**：

```
t=0s    ┌─────────────┐
        │  First Run  │  创建记忆，存储对话
        └─────────────┘
           ↓ 7 个查询（每个查询用新的 session_id）
           ↓ Redis: 创建 7 个 memory 键，每个 TTL=10s

t=2s    ┌─────────────┐
        │ Second Run  │  记忆仍有效，成功检索 ✅
        └─────────────┘
           ↓ 2s < 10s → 记忆仍在 Redis
           ↓ 能通过 load_memory 检索到 Alice 和 blue

t=10s   ⏰ Redis 自动过期机制触发
        └─ Redis 自动删除所有过期键（距 First Run 10s）

t=32s   ┌─────────────┐
        │  Third Run  │  记忆已过期，无法检索 ❌
        └─────────────┘
           ↓ 32s > 10s → Redis 已自动删除所有过期键
           ↓ load_memory 返回空数组
           ↓ 从干净状态重新开始
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
    session_id = f"redis_memory_session_{index}"    # session_0, session_1, ...
```

**查询设计意图**：

| 查询 | 目的 | First Run | Second Run | Third Run |
|-----|------|-----------|-----------|-----------|
| **Q1** | 测试初始状态 | ❌ 不记得 | ✅ 记得（Alice） | ❌ 不记得（已过期） |
| **Q2** | 测试颜色记忆 | ❌ 不记得 | ✅ 记得（blue） | ❌ 不记得（已过期） |
| **Q3** | 测试工具 | ✅ 正常 | ✅ 正常 | ✅ 正常 |
| **Q4** | 建立记忆 | 📝 存储名字 | 📝 追加记忆 | 📝 重新存储 |
| **Q5** | 验证名字记忆 | ✅ 记得（刚存储的） | ✅ 记得（跨会话） | ✅ 记得（当前 Run） |
| **Q6** | 存储颜色 | 💾 保存到 Redis | 💾 追加到 Redis | 💾 重新保存 |
| **Q7** | 验证颜色记忆 | ✅ 记得（刚存储的） | ✅ 记得（跨会话） | ✅ 记得（当前 Run） |

---

## Redis 数据结构

### Memory 键的命名规则

```bash
memory:{app_name}/{user_id}:{session_id}
```

**示例**：
```bash
memory:weather_agent_demo/redis_memory_user:redis_memory_session_0
memory:weather_agent_demo/redis_memory_user:redis_memory_session_1
memory:weather_agent_demo/redis_memory_user:redis_memory_session_2
...
```

### 数据类型：List

每个 Memory 键存储一个 **List**，包含该会话的所有事件（JSON 格式）：

```bash
# 查看 session_3 的记忆
redis-cli> LRANGE memory:weather_agent_demo/redis_memory_user:redis_memory_session_3 0 -1

# 输出（简化）:
1) {
     "content": {
       "parts": [{"text": "Hello! My name is Alice. What's your name?"}],
       "role": null
     },
     "author": "user",
     "timestamp": "2026-02-03T21:46:57.860020"
   }

2) {
     "content": {
       "parts": [{"text": "Hello, Alice! My name is [assistant]..."}],
       "role": "model"
     },
     "author": "assistant",
     "timestamp": "2026-02-03T21:46:58.480070"
   }
```

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
| **Redis 键** | `session:app:user:session_id` | `memory:app/user:session_id` |
| **本示例** | 每个查询独立会话 | 跨查询共享记忆 |

### 示例说明

**Session State**（会话状态）：
```python
# 会话 1
User: "My name is Alice"
→ Session State: 存储在 session:app:user:session_1 中
→ 仅 session_1 可访问

# 会话 2（新会话）
User: "What's my name?"
→ Session State: session_2 没有 session_1 的数据
→ 无法回答
```

**Memory**（跨会话记忆）：
```python
# 会话 1
User: "My name is Alice"
→ Memory: 存储到 memory:app/user:session_1 中
→ 所有会话可通过 load_memory 检索

# 会话 2（新会话）
User: "What's my name?"
→ 调用 load_memory("name") → 检索所有 memory 键 → 找到 "Alice"
→ 成功回答
```

---

## 运行结果分析

### 完整输出

```txt
python3 examples/memory_service_with_redis/run_agent.py
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
🤖 Assistant: Hello, Alice! My name is [assistant]. How can I assist you today?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": [... "My name is Alice" ...]}'}]
Yes, I remember! Your name is Alice. How can I assist you today?
----------------------------------------
🤖 Assistant: Hello! As an AI, I don't have personal preferences, but I think blue is a fantastic choice—it's often associated with calmness and the sky!
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": [... "favorite color is blue" ...]}'}]
Yes, I remember! Your favorite color is blue. It's a great choice—calm and serene, like the sky!
----------------------------------------
============================================================
Second run
============================================================
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": [... Alice ...]}'}]
Yes, I remember! Your name is Alice. How can I assist you today?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": [... blue ...]}'}]
Yes, I remember! Your favorite color is blue. It's a great choice—calm and serene, like the sky!
----------------------------------------
...
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
```

### 关键对比：三次运行的行为差异

#### 📊 对比表格

| 问题 | First Run (t=0s) | Second Run (t=2s) | Third Run (t=32s) |
|------|------------------|-------------------|-------------------|
| **"Do you remember my name?"** | ❌ `memories: []`<br/>"I don't have your name stored..." | ✅ `memories: [... Alice ...]`<br/>"Yes! Your name is Alice" | ❌ `memories: []`<br/>"I don't have any memory..." |
| **"Do you remember my favorite color?"** | ❌ `memories: []`<br/>"I don't have any memory..." | ✅ `memories: [... blue ...]`<br/>"Yes! ...blue" | ❌ `memories: []`<br/>"I don't have any memory..." |
| **Memory 状态** | 🆕 空（无记忆） | ✅ 存在（距 First Run 2s） | 🗑️ 已清理（Redis 自动过期） |
| **Redis 键数** | 0 → 7 个键 | 14 个键（First+Second） | 7 个键（仅 Third） |

#### 🔍 详细分析

**1️⃣ First Run（初始状态，建立记忆）**

```txt
Q1: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: []  ❌ 空数组
    💬 "I don't have your name stored in my memory."

Q4: "Hello! My name is Alice."
    💾 存储到 Redis 中（自动触发）
    💾 Redis: RPUSH + EXPIRE 10s

Q5: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: [... Alice ...]  ✅ 检索成功
    💬 "Yes, I remember! Your name is Alice."
```

- **状态**: Memory 初始为空
- **Redis**: 空 → First Run 后创建 7 个 memory 键
- **原因**: Redis 中还没有任何记忆数据
- **结果**: Q1-Q2 检索失败，Q5-Q7 检索成功（因为 Q4、Q6 已存储到 Redis）

**Redis 数据变化**：

```bash
# First Run 开始前
redis-cli> KEYS memory:*
(empty array)

# First Run 结束后
redis-cli> KEYS memory:*
1) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_0"
2) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_1"
3) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_2"
4) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_3"
5) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_4"
6) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_5"
7) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_6"

# 查看 session_3 的记忆（Q4: "My name is Alice"）
redis-cli> LRANGE memory:weather_agent_demo/redis_memory_user:redis_memory_session_3 0 -1
1) "{\"content\": {\"parts\": [{\"text\": \"Hello! My name is Alice...\"}]}, ...}"
2) "{\"content\": {\"parts\": [{\"text\": \"Hello, Alice! ...\"}]}, ...}"

redis-cli> TTL memory:weather_agent_demo/redis_memory_user:redis_memory_session_3
(integer) 8  # 还有 8 秒过期
```

---

**2️⃣ Second Run（2 秒后，记忆仍有效）**

```txt
Q1: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: [... Alice ...]  ✅ 检索成功
    💬 "Yes, I remember! Your name is Alice."

Q2: "Do you remember my favorite color?"
    🔧 load_memory(query="favorite color")
    📊 Result: memories: [... blue ...]  ✅ 检索成功
    💬 "Yes, I remember! Your favorite color is blue."
```

- **状态**: 距 First Run 仅 2 秒，记忆仍在 TTL 有效期内（10 秒）
- **Redis**: First Run 的 7 个键仍然存在，TTL 剩余约 8 秒
- **原因**: `RedisMemoryService` 从 Redis 检索到 First Run 的所有对话事件
- **结果**: Agent 成功从 Memory 中检索到名字和颜色

**Redis 数据状态**：

```bash
# Second Run 开始前
redis-cli> KEYS memory:*
1) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_0"
...
7) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_6"
# First Run 的 7 个键都还在

redis-cli> TTL memory:weather_agent_demo/redis_memory_user:redis_memory_session_3
(integer) 8  # 还有 8 秒

# Second Run 会话恢复流程：
# 1. load_memory("user's name") 触发
# 2. RedisMemoryService.search_memory() 执行
# 3. 遍历所有 memory:weather_agent_demo/redis_memory_user:* 键
# 4. LRANGE 读取每个 List 的所有事件
# 5. 过滤出包含 "name" 关键词的事件
# 6. 返回匹配的事件列表
```

**为什么能记住？**

1. **跨会话共享**：Second Run 使用新的 session_id（`session_7`, `session_8`, ...），但能检索到 First Run 的记忆
2. **语义检索**：`load_memory("user's name")` 在所有 `memory:*` 键中搜索包含 "name" 的事件
3. **TTL 未过期**：2s < 10s，First Run 的键仍在 Redis 中
4. **Redis 持久化**：数据存储在 Redis，不受进程重启影响

---

**3️⃣ Redis 自动过期（t=10s~32s 之间）**

```txt
⏰ t=10s: Redis 自动删除 First Run 的所有过期键
⏰ t=12s: Redis 自动删除 Second Run 的所有过期键
```

**Redis 自动过期机制**：

- **无需清理任务**：不像 `InMemoryMemoryService` 需要后台清理任务
- **Redis 原生支持**：Redis 会自动删除 TTL 到期的键
- **精确到秒**：每个键独立计时，到期即删除
- **无性能开销**：不需要扫描所有数据，Redis 内部高效处理

**验证过期**：

```bash
# 在 t=11s 查询 Redis
redis-cli> KEYS memory:*
(empty array)  ✅ First Run 的键已全部过期删除

redis-cli> EXISTS memory:weather_agent_demo/redis_memory_user:redis_memory_session_0
(integer) 0  ❌ 不存在
```

---

**4️⃣ Third Run（32 秒后，记忆已过期）**

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

- **状态**: 距 First Run 32 秒，记忆已超过 TTL（10 秒）
- **Redis**: 所有 First Run 和 Second Run 的键都已被 Redis 自动删除
- **原因**: Redis 的 EXPIRE 机制自动清理过期键
- **结果**: Agent 无法从 Memory 中检索到任何数据，行为与 First Run Q1-Q2 相同

**Redis 数据状态**：

```bash
# Third Run 开始前（距 First Run 32 秒）
redis-cli> KEYS memory:*
(empty array)  ❌ 所有旧记忆都已过期删除

# Third Run Q4 后（重新建立记忆）
redis-cli> KEYS memory:*
1) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_17"  # Q4 新建
2) "memory:weather_agent_demo/redis_memory_user:redis_memory_session_18"  # Q5 新建
...
```

**为什么不记得了？**

1. **TTL 过期**：32s > 10s TTL
2. **Redis 自动删除**：Redis 的 EXPIRE 机制自动清理过期键
3. **检索失败**：`load_memory` 在空 Redis 中搜索，返回空数组
4. **重新开始**：从干净状态重新开始，需要重新建立记忆

---

### 💡 核心功能验证

#### ✅ **Redis 持久化**
- Second Run 能访问 First Run 的数据
- 数据存储在 Redis，进程重启后仍可恢复
- **结论**: Redis 持久化和恢复正常工作

#### ✅ **跨会话共享**
- Second Run 使用新的 session_id，但能检索到 First Run 的记忆
- **结论**: 跨会话共享正常工作

#### ✅ **TTL 缓存淘汰（Redis 自动过期）**
- TTL 设置为 **10 秒**
- Second Run（2 秒后）能检索到记忆
- Third Run（32 秒后）无法检索到记忆
- **结论**: Redis EXPIRE 机制正常工作，自动清理过期键

#### ✅ **无需后台清理任务**
- 没有清理任务日志输出
- Redis 自动处理过期键删除
- **结论**: Redis 原生过期机制高效且可靠

#### ✅ **语义搜索**
- `load_memory("user's name")` 能检索到包含 "Alice" 的事件
- `load_memory("favorite color")` 能检索到包含 "blue" 的事件
- **结论**: 语义搜索功能正常工作

#### ✅ **分布式支持**
- 使用 Redis 外部存储，支持跨进程、跨服务器共享记忆
- **结论**: 适合生产环境和分布式部署

---

## 实现逻辑说明

### 为什么会有三次不同的行为？

**核心机制**：TTL（Time-To-Live）+ Redis 自动过期

```python
# 代码实现（run_agent.py）
async def main():
    # First run
    await run_weather_agent()        # t=0s, 建立记忆
    await asyncio.sleep(2)           # 等待 2 秒

    # Second run
    await run_weather_agent()        # t=2s, 2 < 10（TTL 未过期）
    await asyncio.sleep(30)          # 等待 30 秒

    # Third run
    await run_weather_agent()        # t=32s, 32 > 10（TTL 已过期）
```

**为什么 Second Run 能检索到记忆？**

1. **数据保存**：First Run 结束时，Redis 保存了 7 个 memory 键
2. **Redis EXPIRE**：每个键都设置了 `EXPIRE 10` 秒
3. **时间未到**：Second Run 在 2 秒后运行，2s < 10s，数据仍在 Redis
4. **跨会话检索**：虽然使用新的 session_id，但 `load_memory` 能检索所有 memory 键

**为什么 Third Run 无法检索到记忆？**

1. **时间到期**：Third Run 在 32 秒后运行，32s > 10s
2. **Redis 自动删除**：Redis 的 TTL 机制在 10 秒后自动删除了所有过期键
3. **检索失败**：`load_memory` 在空 Redis 中搜索，返回空数组
4. **重新开始**：从干净状态重新开始，需要重新建立记忆

---

### Redis 命令序列分析

**First Run Q4（保存记忆）时的 Redis 操作**：

```bash
# 1. 存储事件到 List（使用 RPUSH 追加）
RPUSH memory:weather_agent_demo/redis_memory_user:redis_memory_session_3 '{"content": {...}, "author": "user", ...}'
RPUSH memory:weather_agent_demo/redis_memory_user:redis_memory_session_3 '{"content": {...}, "author": "assistant", ...}'

# 2. 设置过期时间（Redis 自动过期）
EXPIRE memory:weather_agent_demo/redis_memory_user:redis_memory_session_3 10
```

**Second Run Q1（检索记忆）时的 Redis 操作**：

```bash
# 1. 查询所有 memory 键
KEYS memory:weather_agent_demo/redis_memory_user:*
# 返回: [session_0, session_1, ..., session_6]  ✅ 7 个键都还在

# 2. 读取每个键的事件列表
LRANGE memory:weather_agent_demo/redis_memory_user:redis_memory_session_0 0 -1
LRANGE memory:weather_agent_demo/redis_memory_user:redis_memory_session_1 0 -1
...
LRANGE memory:weather_agent_demo/redis_memory_user:redis_memory_session_6 0 -1

# 3. 在内存中过滤匹配 "name" 关键词的事件
# 4. 返回匹配的事件列表

# ⚠️ 注意：Redis 读取操作不会刷新 TTL
# RedisMemoryService 只在写入时设置 EXPIRE
```

**Third Run Q1（检索失败）时的 Redis 操作**：

```bash
# 1. 查询所有 memory 键
KEYS memory:weather_agent_demo/redis_memory_user:*
# 返回: (empty array)  ❌ 所有键都已过期删除

# 2. 无数据可读，返回空数组
```

---

### Redis 自动过期 vs 定期清理任务

#### 对比表格

| 特性 | InMemoryMemoryService<br/>（定期清理任务） | RedisMemoryService<br/>（Redis 自动过期） |
|-----|----------------------------------|------------------------------|
| **清理方式** | 后台任务每 N 秒扫描一次 | Redis 自动删除过期键 |
| **清理精度** | 取决于扫描间隔 | 精确到秒 |
| **性能开销** | 需要遍历所有数据 | Redis 内部高效处理 |
| **日志输出** | 清理时输出日志 | 无日志（静默删除） |
| **TTL 刷新** | 不支持（只在写入时设置） | 不支持（只在写入时设置） |
| **适用场景** | 内存存储 | 外部存储（Redis） |

#### Redis 自动过期的优势

1. **无需后台任务**：不需要定期扫描，减少 CPU 开销
2. **精确到秒**：Redis 保证键在 TTL 到期时被删除
3. **高效**：Redis 内部使用高效的数据结构管理过期键
4. **可靠**：即使应用程序崩溃，Redis 仍会清理过期数据

---

### load_memory 工具的工作原理

**工具定义**：

```python
async def load_memory(query: str, tool_context: InvocationContext) -> dict:
    """从 Memory 中检索相关记忆

    Args:
        query: 查询关键词（如 "user's name", "favorite color"）
        tool_context: 工具上下文（包含 memory_service）

    Returns:
        dict: {"memories": [Event, ...]}
    """
    memory_service = tool_context.agent_context.memory_service
    memories = await memory_service.search_memory(query, ...)
    return {"result": json.dumps({"memories": memories})}
```

**RedisMemoryService 搜索逻辑**（简化）：

```python
async def search_memory(self, query: str) -> List[Event]:
    """语义搜索相关记忆"""
    words_in_query = extract_words_lower(query)  # 提取关键词
    matched_events = []

    # 1. 获取所有 memory 键
    pattern = f"memory:{app_name}/{user_id}:*"
    keys = await redis.keys(pattern)

    # 2. 遍历每个键，读取事件列表
    for key in keys:
        events_json_list = await redis.lrange(key, 0, -1)

        for event_json in events_json_list:
            event = json.loads(event_json)

            # 3. 提取事件中的关键词
            words_in_event = extract_words_lower(event)

            # 4. 模糊匹配（支持部分匹配）
            if fuzzy_match(words_in_query, words_in_event):
                matched_events.append(event)

    return matched_events
```

**匹配示例**：

```python
# Query: "user's name"
# 提取关键词: ["user", "name"]

# Event 1: "Do you remember my name?"
# 提取关键词: ["remember", "name"]
# 匹配: "name" in both → ✅ 匹配

# Event 2: "Hello! My name is Alice."
# 提取关键词: ["hello", "name", "alice"]
# 匹配: "name" in both → ✅ 匹配

# Event 3: "What is the weather in Paris?"
# 提取关键词: ["weather", "paris"]
# 匹配: no common words → ❌ 不匹配
```

---

## 总结

本示例成功演示了 **RedisMemoryService** 的核心能力：

1. **Redis 持久化**: 在 Redis 中持久化跨会话记忆数据
2. **跨会话共享**: 不同会话可以访问共享的记忆数据
3. **TTL 缓存淘汰**: Redis 自动过期机制避免内存泄漏
4. **语义搜索**: 通过关键词检索相关记忆
5. **自动清理**: Redis EXPIRE 命令自动清理过期键，无需后台任务
6. **分布式支持**: 支持跨进程、跨服务器共享记忆

### 适用场景

- ✅ 生产环境（数据持久化）
- ✅ 分布式部署（跨进程共享）
- ✅ 高可用场景（Redis 集群）
- ✅ 跨会话知识共享
- ✅ 长期记忆管理（配合合理 TTL）

### Memory Service 对比

| 特性 | InMemoryMemoryService | RedisMemoryService | SqlMemoryService |
|-----|----------------------|-------------------|------------------|
| **数据存储** | 进程内存 | Redis 外部存储 | MySQL/PostgreSQL |
| **持久化** | ❌ 进程重启丢失 | ✅ 持久化到 Redis | ✅ 持久化到数据库 |
| **分布式** | ❌ 无法跨进程共享 | ✅ 支持跨进程/服务器 | ✅ 支持跨进程/服务器 |
| **TTL 机制** | ✅ 定期清理任务 | ✅ **Redis 自动过期** | ✅ 定期清理任务 |
| **清理效率** | ⭐⭐⭐ 需要扫描 | ⭐⭐⭐⭐⭐ **Redis 原生** | ⭐⭐⭐ 需要扫描 |
| **部署场景** | 本地开发/单机 | 生产环境/分布式 | 生产环境/分布式 |

💡 **选择建议**:
- 开发测试用 `InMemoryMemoryService`
- 生产环境用 `RedisMemoryService`（推荐，高性能 + 自动过期）
- 需要复杂查询用 `SqlMemoryService`

### Redis 自动过期的优势

相比 `InMemoryMemoryService` 的定期清理任务，`RedisMemoryService` 使用 Redis 自动过期机制具有以下优势：

1. **无需后台任务**：不需要启动清理线程，减少系统资源消耗
2. **精确到秒**：Redis 保证键在 TTL 到期时被删除
3. **高效**：Redis 内部使用高效的数据结构管理过期键，无需扫描所有数据
4. **可靠**：即使应用程序崩溃，Redis 仍会清理过期数据
5. **无日志噪音**：静默删除，不产生大量清理日志

💡 **生产环境推荐**: 优先使用 `RedisMemoryService`，利用 Redis 原生的过期机制，获得更好的性能和可靠性！
