# Memory Service 内存存储示例

## 示例简介

本示例演示如何使用 **InMemoryMemoryService** 实现跨会话的记忆管理，并展示 **TTL（Time-To-Live）缓存淘汰机制** 的效果。

### 核心特性

- ✅ **内存存储**: 使用 `InMemoryMemoryService` 在内存中存储跨会话记忆数据
- ✅ **跨会话共享**: 不同会话（session）可以共享同一份记忆数据
- ✅ **TTL 缓存淘汰**: 配置记忆 10 秒 TTL，演示过期自动清理
- ✅ **语义搜索**: 通过 `load_memory` 工具根据查询关键词检索相关记忆
- ✅ **自动清理**: 过期记忆自动清理，避免内存泄漏

## 环境要求

- Python 3.10+（强烈建议使用 3.12）

## 安装和运行

### 1. 下载并安装 trpc-agent

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 2. 配置环境变量

在 `.env` 文件中设置 LLM 相关变量（或通过 export 设置）:
- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 3. 运行示例

```bash
python3 examples/memory_service_with_in_memory/run_agent.py
```

## 代码说明

### MemoryService 配置

```python
def create_memory_service():
    """创建 Memory Service"""

    memory_service_config = MemoryServiceConfig(
        enabled=True,                      # 启用 Memory 功能
        ttl=MemoryServiceConfig.create_ttl_config(
            enable=True,                   # 启用 TTL
            ttl_seconds=10,                # 记忆过期时间：10 秒
            cleanup_interval_seconds=10    # 清理间隔：10 秒
        ),
    )

    memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)
    return memory_service
```

**配置说明**：

| 参数 | 值 | 说明 | 生产环境建议 |
|-----|---|------|------------|
| `enabled` | True | 启用 Memory 功能 | True |
| `ttl_seconds` | 10 | 记忆过期时间 | 86400（24小时）或更长 |
| `cleanup_interval_seconds` | 10 | 清理间隔 | 3600（1小时） |

⚠️ **重要提示**：本示例将 TTL 设置为 **10 秒**，是为了快速演示缓存淘汰行为。**生产环境请设置更合理的值**！

---

### 测试流程

示例运行三次相同的对话，每次间隔不同，用于演示 TTL 缓存淘汰效果：

```python
async def main():
    memory_service = create_memory_service()

    print("First run")
    await run_weather_agent(memory_service)        # 运行 7 个查询

    await asyncio.sleep(2)                         # 等待 2 秒（< 10秒 TTL）

    print("Second run")
    await run_weather_agent(memory_service)        # 再次运行 7 个查询

    await asyncio.sleep(30)                        # 等待 30 秒（> 10秒 TTL）

    print("Third run")
    await run_weather_agent(memory_service)        # 第三次运行
```

**时间线**：

```
t=0s    ┌─────────────┐
        │  First Run  │  创建记忆，存储对话
        └─────────────┘
           ↓ 7 个查询（每个查询用新的 session_id）
           ↓ Memory: 存储所有对话事件，TTL=10s

t=2s    ┌─────────────┐
        │ Second Run  │  记忆仍有效，成功检索 ✅
        └─────────────┘
           ↓ 2s < 10s → 记忆仍在内存
           ↓ 能通过 load_memory 检索到 Alice 和 blue

t=10s   ⏰ 清理任务启动，扫描过期记忆
        └─ 发现过期数据（距 First Run 10s）
           └─ 删除所有过期事件

t=32s   ┌─────────────┐
        │  Third Run  │  记忆已过期，无法检索 ❌
        └─────────────┘
           ↓ 32s > 10s → 记忆已被清理
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
    session_id = f"in_memory_session_{index}"       # session_0, session_1, ...
```

**关键设计**：

- **不同 session_id**：每个查询使用独立的会话 ID（`session_0`, `session_1`, ...）
- **共享 Memory**：所有会话共享同一个 `InMemoryMemoryService`
- **跨会话检索**：通过 `load_memory` 工具检索跨会话的记忆

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
| **本示例** | 每个查询独立会话 | 跨查询共享记忆 |

### 示例说明

**Session State**（会话状态）：
```python
# 会话 1
User: "My name is Alice"
→ Session State: 存储在 session_1 中
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
→ Memory: 存储到共享 Memory 中
→ 所有会话可访问

# 会话 2（新会话）
User: "What's my name?"
→ 调用 load_memory("name") → 检索到 "Alice"
→ 成功回答
```

---

## 运行结果分析

### 完整输出

```txt
python3 examples/memory_service_with_in_memory/run_agent.py
============================================================
First run
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
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is sunny with a temperature of 25 degrees Celsius.
----------------------------------------
🤖 Assistant: Hello, Alice! My name is [assistant]. How can I assist you today?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": [{"content": {"parts": [{"text": "Do you remember my name?"}]}, ...}, {"content": {"parts": [{"text": "Hello! My name is Alice. What's your name?"}]}, ...}]}'}]
Yes, I remember! Your name is Alice. How can I assist you today?
----------------------------------------
🤖 Assistant: Hello! As an AI, I don't have personal preferences, but I think blue is a fantastic choice—it's often associated with calmness and the sky!
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": [{"content": {"parts": [{"text": "Do you remember my favorite color?"}]}, ...}, {"content": {"parts": [{"text": "Hello! My favorite color is blue. What's your favorite color?"}]}, ...}]}'}]
Yes, I remember! Your favorite color is blue. What would you like to know or discuss about it?
----------------------------------------
============================================================
Second run
============================================================
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': "user's name"})]
📊 [Tool Result: {'result': '{"memories": [... Alice ...]'}]
Yes, I remember! Your name is Alice. How can I assist you today?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: load_memory({'query': 'favorite color'})]
📊 [Tool Result: {'result': '{"memories": [... blue ...]'}]
Yes, I remember! Your favorite color is blue. What would you like to know or discuss about it?
----------------------------------------
...
[2026-02-03 21:43:57][INFO] Cleaned up expired event: weather_agent_demo/in_memory_user/in_memory_session_0/...
[2026-02-03 21:43:57][INFO] Cleaned up expired event: weather_agent_demo/in_memory_user/in_memory_session_1/...
[2026-02-03 21:43:57][INFO] Cleaned up expired event: weather_agent_demo/in_memory_user/in_memory_session_2/...
... (共清理 24 个过期事件)
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
| **"Do you remember my name?"** | ❌ `memories: []`<br/>"I don't have any memory..." | ✅ `memories: [... Alice ...]`<br/>"Yes, I remember! Your name is Alice" | ❌ `memories: []`<br/>"I don't have any memory..." |
| **"Do you remember my favorite color?"** | ❌ `memories: []`<br/>"I don't have any memory..." | ✅ `memories: [... blue ...]`<br/>"Yes, I remember! ...blue" | ❌ `memories: []`<br/>"I don't have any memory..." |
| **Memory 状态** | 🆕 空（无记忆） | ✅ 存在（距 First Run 2s） | 🗑️ 已清理（距 Second Run 30s） |
| **清理日志** | 无 | 在 Second/Third 之间清理 | 无（已清理完毕） |

#### 🔍 详细分析

**1️⃣ First Run（初始状态，建立记忆）**

```txt
Q1: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: []  ❌ 空数组
    💬 "I don't have any memory of your name."

Q4: "Hello! My name is Alice."
    💾 存储到 Memory 中（自动触发）

Q5: "Do you remember my name?"
    🔧 load_memory(query="user's name")
    📊 Result: memories: [... Alice ...]  ✅ 检索成功
    💬 "Yes, I remember! Your name is Alice."
```

- **状态**: Memory 初始为空
- **原因**: 内存中还没有任何记忆数据
- **结果**: Q1-Q2 检索失败，Q5-Q7 检索成功（因为 Q4、Q6 已建立记忆）

**Memory 数据结构**（First Run 后）：

```python
InMemoryMemoryService._session_events = {
    "weather_agent_demo/in_memory_user": {
        "in_memory_session_0": [Event(...)],  # Q1 的对话
        "in_memory_session_1": [Event(...)],  # Q2 的对话
        "in_memory_session_2": [Event(...)],  # Q3 的对话
        "in_memory_session_3": [Event(...)],  # Q4 的对话（包含 "My name is Alice"）
        "in_memory_session_4": [Event(...)],  # Q5 的对话
        "in_memory_session_5": [Event(...)],  # Q6 的对话（包含 "favorite color is blue"）
        "in_memory_session_6": [Event(...)],  # Q7 的对话
    }
}
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
- **原因**: `InMemoryMemoryService` 保存了 First Run 的所有对话事件
- **结果**: Agent 成功从 Memory 中检索到名字和颜色

**为什么能记住？**

1. **跨会话共享**：Second Run 使用新的 session_id（`session_0`, `session_1`, ...），但 Memory 是共享的
2. **语义检索**：`load_memory("user's name")` 检索包含 "name" 关键词的事件
3. **TTL 未过期**：2s < 10s，记忆仍在内存中

---

**3️⃣ 清理任务执行（t=10s~32s 之间）**

```txt
[2026-02-03 21:43:57][INFO] Cleaned up expired event: weather_agent_demo/in_memory_user/in_memory_session_0/d1e29f5f...
[2026-02-03 21:43:57][INFO] Cleaned up expired event: weather_agent_demo/in_memory_user/in_memory_session_1/0f2ab18f...
[2026-02-03 21:43:57][INFO] Cleaned up expired event: weather_agent_demo/in_memory_user/in_memory_session_2/5796a390...
... (共清理 24 个过期事件)
```

- **时间点**: 距 First Run 约 10-30 秒
- **清理逻辑**: 清理任务每 10 秒扫描一次，删除 `timestamp` 超过 10 秒的事件
- **清理数量**: 24 个事件（First Run 7 个查询 + Second Run 7 个查询，每个查询约 2-4 个事件）

**清理任务工作原理**：

```python
def _cleanup_expired(self) -> None:
    now = time.time()
    for key, events in self._session_events.items():
        for session_id, event_list in events.items():
            for event_ttl in event_list:
                # 检查是否过期
                if event_ttl.is_expired(now):
                    # 删除过期事件
                    self._session_events[key][session_id].remove(event_ttl)
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

- **状态**: 距 First Run 32 秒，记忆已超过 TTL（10 秒）
- **原因**: 清理任务删除了所有过期事件
- **结果**: Agent 无法从 Memory 中检索到任何数据，行为与 First Run Q1-Q2 相同

**为什么不记得了？**

1. **TTL 过期**：32s > 10s TTL
2. **清理任务删除**：后台清理任务删除了所有过期事件
3. **Memory 为空**：`_session_events` 已被清空
4. **重新开始**：从干净状态重新开始，需要重新建立记忆

---

### 💡 核心功能验证

#### ✅ **内存存储**
- Memory 数据存储在 Python 进程内存中
- **结论**: 内存存储正常工作，适合单机场景

#### ✅ **跨会话共享**
- Second Run 使用新的 session_id，但能检索到 First Run 的记忆
- **结论**: 跨会话共享正常工作

#### ✅ **TTL 缓存淘汰**
- TTL 设置为 **10 秒**
- Second Run（2 秒后）能检索到记忆
- Third Run（32 秒后）无法检索到记忆
- **结论**: TTL 缓存淘汰机制正常工作

#### ✅ **自动清理**
- 清理间隔设置为 **10 秒**
- 日志显示清理了 24 个过期事件
- **结论**: 后台清理任务正常运行，内存得到释放

#### ✅ **语义搜索**
- `load_memory("user's name")` 能检索到包含 "Alice" 的事件
- `load_memory("favorite color")` 能检索到包含 "blue" 的事件
- **结论**: 语义搜索功能正常工作

---

## 实现逻辑说明

### 为什么会有三次不同的行为？

**核心机制**：TTL（Time-To-Live）+ 定期清理任务

```python
# 代码实现（run_agent.py）
async def main():
    memory_service = create_memory_service()

    # First run
    await run_weather_agent(memory_service)        # t=0s, 建立记忆
    await asyncio.sleep(2)                         # 等待 2 秒

    # Second run
    await run_weather_agent(memory_service)        # t=2s, 2 < 10（TTL 未过期）
    await asyncio.sleep(30)                        # 等待 30 秒

    # Third run
    await run_weather_agent(memory_service)        # t=32s, 32 > 10（TTL 已过期）
```

**为什么 Second Run 能检索到记忆？**

1. **数据保存**：First Run 结束时，内存保存了所有对话事件
2. **时间未到**：Second Run 在 2 秒后运行，2s < 10s，数据未过期
3. **跨会话共享**：虽然使用新的 session_id，但 Memory 是共享的
4. **语义检索**：`load_memory` 工具在共享 Memory 中搜索相关事件

**为什么 Third Run 无法检索到记忆？**

1. **时间到期**：Third Run 在 32 秒后运行，32s > 10s
2. **清理任务删除**：后台清理任务定期扫描并删除过期事件
3. **检索失败**：`load_memory` 工具在空 Memory 中搜索，返回空数组
4. **重新开始**：从干净状态重新开始，需要重新建立记忆

---

### 清理任务工作原理

**清理任务代码逻辑**（简化）：

```python
async def _cleanup_loop(self) -> None:
    """后台清理任务循环"""
    while not self._cleanup_stop_event.is_set():
        await asyncio.sleep(self.cleanup_interval_seconds)  # 等待 10 秒
        self._cleanup_expired()  # 清理过期事件

def _cleanup_expired(self) -> None:
    """清理过期事件"""
    now = time.time()
    removed_events = {}

    # Phase 1: 收集过期事件
    for key, events in self._session_events.items():
        for session_id, event_list in events.items():
            for event_ttl in event_list:
                if event_ttl.is_expired(now):  # 检查是否过期
                    removed_events[key][session_id].append(event_ttl)

    # Phase 2: 删除过期事件
    for key, events in removed_events.items():
        for session_id, event_list in events.items():
            for event_ttl in event_list:
                self._session_events[key][session_id].remove(event_ttl)
```

**清理任务执行时间线**：

```
t=0s    First Run 开始
        ↓
t=10s   清理任务第 1 次执行
        ↓ 检查 First Run 的事件（距离 10s）
        ↓ is_expired() = True → 删除

t=20s   清理任务第 2 次执行
        ↓ 检查 Second Run 的事件（距离 18s）
        ↓ is_expired() = True → 删除

t=30s   清理任务第 3 次执行
        ↓ 无过期事件
```

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

**搜索逻辑**（简化）：

```python
def search_memory(self, query: str) -> List[Event]:
    """语义搜索相关记忆"""
    words_in_query = extract_words_lower(query)  # 提取关键词
    matched_events = []

    # 遍历所有事件
    for key, events in self._session_events.items():
        for session_id, event_list in events.items():
            for event_ttl in event_list:
                # 提取事件中的关键词
                words_in_event = extract_words_lower(event_ttl.event)

                # 模糊匹配（支持部分匹配）
                if fuzzy_match(words_in_query, words_in_event):
                    matched_events.append(event_ttl.event)

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

本示例成功演示了 **InMemoryMemoryService** 的核心能力：

1. **内存存储**: 在 Python 进程内存中存储跨会话记忆数据
2. **跨会话共享**: 不同会话可以访问共享的记忆数据
3. **TTL 缓存淘汰**: 自动过期和清理机制避免内存泄漏
4. **语义搜索**: 通过关键词检索相关记忆
5. **自动清理**: 后台任务定期清理过期数据
6. **零依赖**: 无需外部存储，适合快速原型开发

### 适用场景

- ✅ 本地开发和测试
- ✅ 单机部署（非分布式）
- ✅ 短期记忆管理（配合 TTL）
- ✅ 跨会话知识共享
- ❌ 生产环境（重启丢失数据）
- ❌ 分布式部署（无法跨进程共享）

### Memory Service 对比

| 特性 | InMemoryMemoryService | RedisMemoryService | SqlMemoryService |
|-----|----------------------|-------------------|------------------|
| **数据存储** | 进程内存 | Redis 外部存储 | MySQL/PostgreSQL |
| **持久化** | ❌ 进程重启丢失 | ✅ 持久化到 Redis | ✅ 持久化到数据库 |
| **分布式** | ❌ 无法跨进程共享 | ✅ 支持跨进程/服务器 | ✅ 支持跨进程/服务器 |
| **TTL 机制** | ✅ 定期清理任务 | ✅ Redis 自动过期 | ✅ 定期清理任务 |
| **部署场景** | 本地开发/单机 | 生产环境/分布式 | 生产环境/分布式 |

💡 **选择建议**: 开发测试用 `InMemoryMemoryService`，生产环境用 `RedisMemoryService` 或 `SqlMemoryService`。
