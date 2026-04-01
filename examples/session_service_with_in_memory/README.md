# Session Service 内存存储示例

## 示例简介

本示例演示如何使用 **InMemorySessionService** 实现会话管理，并展示 **TTL（Time-To-Live）过期机制** 的效果。

### 核心特性

- ✅ **内存存储**: 使用 `InMemorySessionService` 在内存中管理会话状态
- ✅ **TTL 过期**: 配置会话 5 秒 TTL，演示过期自动清理
- ✅ **状态持久化**: 在 TTL 有效期内，会话状态跨多次对话保持
- ✅ **自动清理**: 过期会话、用户状态、应用状态自动清理

## 环境要求

- Python 3.10+（强烈建议使用 3.12）

## 安装和运行

### 1. 下载并安装 trpc-agent

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
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
python3 examples/session_service_with_in_memory/run_agent.py
```

## 代码说明

### SessionService 配置

```python
session_config = SessionServiceConfig(
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,                      # 启用 TTL
        ttl_seconds=5,                    # 会话过期时间：5 秒
        cleanup_interval_seconds=5        # 清理间隔：5 秒
    ),
)
session_service = InMemorySessionService(session_config=session_config)
```

### 测试流程

示例运行三次相同的对话，每次间隔不同，用于演示 TTL 效果：

1. **First run**: 初始对话，建立会话和状态
2. **Second run**: 2 秒后运行，会话仍在有效期内（TTL=5s）
3. **Third run**: 30 秒后运行，会话已过期被清理

## 运行结果分析

### 完整输出

```txt
python3 examples/session_service_with_in_memory/run_agent.py
============================================================
First run
============================================================
🤖 Assistant: [2026-02-03 22:09:50][INFO][trpc_agent_sdk][trpc_agent_sdk/sessions/_in_memory_session_service.py:372][1247921] Cleanup task started with interval: 5.0s
No, I don't have the ability to remember personal details like your name between conversations. How can I assist you today?
----------------------------------------
🤖 Assistant: No, I don't have the ability to remember personal details like your favorite color. However, I'm happy to help you with anything you need! What can I do for you today?
----------------------------------------
🤖 Assistant:
🔧 [Invoke Tool: get_weather_report({'city': 'Paris'})]
📊 [Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}]
The weather in Paris is currently sunny with a temperature of 25 degrees Celsius. Enjoy the sunshine!
----------------------------------------
🤖 Assistant: Hello, Alice! My name is Assistant. It's nice to meet you! How can I assist you today?
----------------------------------------
🤖 Assistant: Yes, you mentioned your name is Alice! How can I assist you today, Alice?
----------------------------------------
🤖 Assistant: Got it, Alice! Your favorite color is blue. I'll keep that in mind for this conversation. How can I assist you today?
----------------------------------------
🤖 Assistant: Yes, Alice! You mentioned that your favorite color is blue. How can I assist you today?
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
[2026-02-03 22:10:20][INFO][trpc_agent_sdk][trpc_agent_sdk/sessions/_in_memory_session_service.py:367][1247921] Cleanup completed: deleted 3 items (1 sessions, 1 user states, 1 app states)
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
[2026-02-03 22:10:52][INFO][trpc_agent_sdk][trpc_agent_sdk/sessions/_in_memory_session_service.py:392][1247921] Cleanup task stopped
```

### 关键对比：三次运行的行为差异

#### 📊 对比表格

| 问题 | First Run (t=0s) | Second Run (t=2s) | Third Run (t=30s) |
|------|------------------|-------------------|-------------------|
| **"Do you remember my name?"** | ❌ "I don't have the ability to remember..." | ✅ "Yes, Alice! I remember..." | ❌ "I don't have the ability to remember..." |
| **"Do you remember my favorite color?"** | ❌ "I don't remember your favorite color..." | ✅ "Yes, Alice! Your favorite color is blue." | ❌ "I don't remember your favorite color..." |
| **会话状态** | 🆕 新建会话 | ✅ 会话存在（距 First Run 2s） | 🗑️ 会话已过期清理（距 Second Run 30s） |

#### 🔍 详细分析

**1️⃣ First Run（初始对话，会话创建）**

```txt
🤖 "Do you remember my name?"
   → ❌ "No, I don't have the ability to remember..."

🤖 "Do you remember my favorite color?"
   → ❌ "No, I don't have the ability to remember..."
```

- **状态**: 会话首次创建
- **原因**: 内存中还没有该用户的历史会话数据
- **结果**: Agent 无法回忆起任何信息

---

**2️⃣ Second Run（2 秒后，会话仍有效）**

```txt
🤖 "Do you remember my name?"
   → ✅ "Yes, Alice! I remember your name is Alice..."

🤖 "Do you remember my favorite color?"
   → ✅ "Yes, Alice! Your favorite color is blue."
```

- **状态**: 距 First Run 仅 2 秒，会话仍在 TTL 有效期内（5 秒）
- **原因**: `InMemorySessionService` 保存了 First Run 的会话历史
- **结果**: Agent 成功回忆起用户名和偏好
- **关键日志**:
  ```txt
  [22:10:20] Cleanup completed: deleted 3 items (1 sessions, 1 user states, 1 app states)
  ```
  在 Second Run 结束后约 30 秒（从 First Run 开始算），清理任务触发，删除过期数据

---

**3️⃣ Third Run（30 秒后，会话已过期）**

```txt
🤖 "Do you remember my name?"
   → ❌ "As an AI, I don't have the ability to remember..."

🤖 "Do you remember my favorite color?"
   → ❌ "No, I don't remember your favorite color..."
```

- **状态**: 距 Second Run 30 秒，会话已超过 TTL（5 秒）并被清理
- **原因**: 清理任务（cleanup interval = 5s）定期删除过期会话
- **结果**: Agent 无法访问历史数据，行为与 First Run 相同

---

### 💡 核心功能验证

#### ✅ **TTL 过期机制**
- TTL 设置为 **5 秒**
- Second Run（2 秒后）能访问会话
- Third Run（30 秒后）无法访问会话
- **结论**: TTL 过期功能正常工作

#### ✅ **自动清理功能**
- 清理间隔设置为 **5 秒**
- 日志显示 `deleted 3 items (1 sessions, 1 user states, 1 app states)`
- **结论**: 定期清理任务正常运行，内存得到释放

#### ✅ **状态持久化**
- 同一 `session_id` 在 TTL 有效期内保持状态
- Second Run 中 Agent 准确回忆起 First Run 的对话内容
- **结论**: 会话状态在有效期内正确持久化

#### ✅ **内存存储**
- 使用 `InMemorySessionService`，无需外部依赖
- 所有数据存储在 Python 进程内存中
- **结论**: 适合开发测试和单机场景

---

## 总结

本示例成功演示了 **InMemorySessionService** 的核心能力：

1. **会话管理**: 在内存中存储和检索会话历史
2. **TTL 控制**: 自动过期和清理机制避免内存泄漏
3. **状态隔离**: 过期后会话完全清除，不影响新会话
4. **零依赖**: 无需 Redis 等外部服务，适合快速原型开发

### 适用场景

- ✅ 本地开发和测试
- ✅ 单机部署（非分布式）
- ✅ 短期会话管理（配合 TTL）
- ❌ 生产环境（重启丢失数据）
- ❌ 分布式部署（无法跨进程共享）

💡 **生产环境建议**: 使用 `RedisSessionService` 或 `SqlSessionService` 实现持久化和分布式支持。
