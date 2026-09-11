# Advanced Memory 本地持久化示例

本示例演示如何使用 `AdvancedMemoryService` 在本地实现持久化的跨会话记忆。
Agent 可以主动保存用户的重要信息，并在后续会话中根据记忆索引查找和读取相关内容。

## 关键特性

- **主动式记忆**：由 Agent 根据对话内容主动调用工具保存长期有效的信息。
- **记忆分类**：每条记忆包含名称、描述、类型、摘要和详细内容。
- **基于记忆索引的记忆召回**：先读取 `MEMORY.md` 索引，再读取匹配的记忆文件，避免检索全部记忆内容。
- **跨会话持久化**：本地记忆默认保存在示例目录下，并按应用和用户进行隔离。（支持 Redis,SQL 存储）

## Agent 层级结构说明

`AdvancedMemoryService` 通过 `Runner` 绑定到 Agent。它负责初始化长期记忆运行时、注入记忆相关指令并安装工具；具体的记忆保存和读取由 Agent 根据工具描述主动完成。

## 关键代码解释

### `save_memory`

保存或更新一条长期记忆，并同步更新 `MEMORY.md` 索引。适合保存用户的稳定偏好、
习惯和其他未来会话仍然有价值的信息。

### `list_memory_index`

读取当前用户的记忆索引。Agent 在需要回忆信息时应先调用这个工具，了解有哪些可用记忆。

### `read_memory`

根据索引中的文件名读取完整记忆内容。这样可以只读取与当前问题相关的记忆。

## 环境要求

- Python 3.10 或更高版本
- 已安装项目依赖
- 一个可访问的 OpenAI 兼容模型服务

在 `.env` 中配置：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://your-llm-endpoint/v1
TRPC_AGENT_MODEL_NAME=your-model-name
```

## 代码构建

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

如果已经在当前项目中创建了 Python 3.10+ 虚拟环境，也可以直接安装：

```bash
python -m pip install -e .
```

## 运行

```bash
cd examples/memory_service_with_advanced_memory
python run_agent.py
```

示例会使用同一用户运行多个会话，验证长期记忆可以在不同会话之间复用。

## 运行结果（实测）

```txt
👤 [session-1] Please remember that my favorite programming language is Python. Save this as a user preference.
[2026-09-11 13:17:51][INFO][trpc_agent_sdk][trpc_agent_sdk/sessions/_in_memory_session_service.py:398][4086343] Cleanup task started with interval: 5.0s
🔧 save_memory({'filename': 'favorite_programming_language.md', 'name': 'Favorite programming language', 'description': "The user's favorite programming language and related preference.", 'memory_type': 'user', 'summary': "The user's favorite programming language is Python.", 'content': "The user's favorite programming language is Python. When discussing languages, choosing examples, or recommending tech stacks, prefer Python."})
📊 {'saved': True, 'filename': 'favorite_programming_language.md', 'path': '/data/workspace/trpc-agent-python-am-service2/examples/memory_service_with_advanced_memory/tenants/advanced_memory_demo/demo-user/MEMORY/favorite_programming_language.md', 'memory_type': 'user', 'updated_at': '2026-09-11T05:17:54.071665+00:00'}
🤖 Done! ✅ I've saved your preference that **Python is your favorite programming language** as a long-term user memory.

I'll keep this in mind across future conversations — for example, when recommending libraries, writing code examples, or discussing tech stacks, I'll lean toward Python by default.

👤 [session-1] I use Python mainly for backend services and data processing.
🤖 That's useful context — let me update your existing memory to include it.


🔧 save_memory({'content': "The user's favorite programming language is Python. They use Python mainly for backend services and data processing. When discussing languages, choosing examples, or recommending tech stacks, prefer Python and focus on backend development and data processing use cases.", 'description': "The user's favorite programming language and how they use it.", 'filename': 'favorite_programming_language.md', 'memory_type': 'user', 'summary': "The user's favorite programming language is Python, used mainly for backend services and data processing.", 'name': 'Favorite programming language'})
📊 {'saved': True, 'filename': 'favorite_programming_language.md', 'path': '/data/workspace/trpc-agent-python-am-service2/examples/memory_service_with_advanced_memory/tenants/advanced_memory_demo/demo-user/MEMORY/favorite_programming_language.md', 'memory_type': 'user', 'updated_at': '2026-09-11T05:17:56.582892+00:00'}
🤖 Got it — I've updated your memory. ✅

......

👤 [session-2] What do you remember about my favorite programming language?
🤖


🔧 read_memory({'filename': 'favorite_programming_language.md'})
📊 {'found': True, 'filename': 'favorite_programming_language.md', 'content': "---\nname: Favorite programming language\ndescription: The user's favorite programming language and how they use it.\ntype: user\nupdated_at: 2026-09-11T05:17:56.582892+00:00\n---\nThe user's favorite programming language is Python. They use Python mainly for backend services and data processing. When discussing languages, choosing examples, or recommending tech stacks, prefer Python and focus on backend development and data processing use cases.\n", 'updated_at': '2026-09-11T05:17:56.582892+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Here's what I remember about your favorite programming language:

**Python** 🐍

From my long-term memory:
- **Python is your favorite programming language**, and you use it mainly for **backend services** and **data processing**.
- When discussing languages, choosing examples, or recommending tech stacks, I should prefer Python and focus on backend development and data processing use cases.

Related preferences I also have on file:
- You like **typed Python code** with clear dataclasses and small, focused modules.
- You prefer **pytest and focused unit tests** for Python testing.
- You like **concise documentation** with runnable commands and examples.

Is there anything else you'd like me to remember or clarify about your language preferences?
```
