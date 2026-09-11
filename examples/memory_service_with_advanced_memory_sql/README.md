# Advanced Memory SQL 持久化示例

本示例演示如何使用 `AdvancedMemoryService` 将长期记忆保存到 SQL 数据库，实现跨会话、跨 Python 进程的持久化记忆。

## 关键特性

- **主动式记忆**：Agent 根据对话内容主动调用工具保存长期有效的信息。
- **记忆分类**：每条记忆包含名称、描述、类型、摘要和详细内容。
- **基于记忆索引的记忆召回**：先读取数据库中的记忆索引，再读取与问题相关的记忆内容。
- **SQL 持久化**：多个进程或实例使用相同的数据库、应用名和用户 ID 时，可以访问同一份长期记忆。

## Agent 层级结构说明

`AdvancedMemoryService` 通过 `Runner` 绑定到 Agent，并根据配置使用 SQL 保存记忆索引和记忆主题。Agent 通过三个工具主动管理长期记忆。

## 关键代码解释

### `save_memory`

保存或更新一条长期记忆，同时更新数据库中的记忆索引。

### `list_memory_index`

读取当前用户的记忆索引，帮助 Agent 找到与当前问题相关的记忆文件。

### `read_memory`

根据索引中的文件名读取完整记忆内容。

## 环境要求

- Python 3.10 或更高版本
- SQLite 或可访问的 MySQL 数据库
- 一个可访问的 OpenAI 兼容模型服务

默认使用 **SQLite**，不需要额外启动数据库：

```dotenv
SQL_URL=sqlite:///advanced-memory-sql-demo.db
SQL_IS_ASYNC=false
```

使用 **MySQL** 时：

```dotenv
SQL_URL=mysql+aiomysql://user:password@localhost:3306/trpc_agent_advanced_memory?charset=utf8mb4
SQL_IS_ASYNC=true
```

## SQL 配置

在当前目录的 `.env` 中配置数据库和模型：

```dotenv
SQL_URL=sqlite:///advanced-memory-sql-demo.db
SQL_IS_ASYNC=false
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://your-llm-endpoint/v1
TRPC_AGENT_MODEL_NAME=your-model-name
```

也可以使用 `MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_HOST`、`MYSQL_PORT` 和 `MYSQL_DB` 由脚本构造 MySQL 连接串。

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
cd examples/memory_service_with_advanced_memory_sql
source ../../.venv/bin/activate
python run_agent.py
```

脚本会依次启动写入和读取两个独立进程，验证数据库中的记忆可以跨进程和不同会话读取。也可以单独运行某个阶段：

```bash
python run_agent.py --phase write
python run_agent.py --phase read
```

首次运行时，SQLite 数据库文件和 Advanced Memory 数据表会自动创建。示例中的记忆 TTL 在代码的 `AdvancedMemoryServiceConfig` 中配置为`memory_ttl_seconds=120`。

## 运行结果（实测）

```txt
=================== WRITE PROCESS ====================

----- Runner A, query 1 -----
📝 user: Do you remember my name?
🔧 tool call: list_memory_index({})
📊 Tool Result: {'index_path': 'advanced-memory://sql/advanced-memory-sql-demo/sql-demo-user/memory/MEMORY.md', 'index': ''}
🤖 Assistant: Let me check my long-term memory.
🔧 tool call: list_memory_index({})
📊 Tool Result: {'index_path': 'advanced-memory://sql/advanced-memory-sql-demo/sql-demo-user/memory/MEMORY.md', 'index': ''}
🤖 Assistant: I checked my long-term memory, but it's currently empty — I don't have any stored details about you yet, including your name. 😊

If you'd like me to remember it for future conversations, just tell me your name (and anything else you'd like me to keep in mind, like preferences or context), and I'll save it right away.

...

----- Runner B, query 2 -----
📝 user: Do you remember my favorite color?
🔧 tool call: read_memory({'filename': 'user-profile.md'})
📊 Tool Result: {'found': True, 'filename': 'user-profile.md', 'content': "---\nname: User profile\ndescription: Basic identity and preferences of the user.\ntype: user\nupdated_at: 2026-09-11T06:00:15.755769+00:00\n---\n---\nname: User profile\ndescription: Basic identity and preferences of the user.\ntype: user\nupdated_at: 2026-09-11T06:00:09.820671+00:00\n---\nThe user's name is Alice. She introduced herself at the start of ourfirst conversation. Her favorite color is blue, which she shared in a later conversation.\n", 'updated_at': '2026-09-11T06:00:15.755769+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Assistant: Yes, I remember! Your favorite color is **blue** — you shared that with me in a later conversation, Alice. 💙
```