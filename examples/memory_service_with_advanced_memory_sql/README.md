# Advanced Memory SQL 示例

本示例使用 SQL 保存 Advanced Memory，并验证同一用户的长期 memory 可以跨 Python 进程和不同 session 读取。

- SQL：`SqlSessionService` + `AdvancedMemoryService`

```text
SqlSessionService
└── Session、app state、user state

AdvancedMemoryService(storage_backend="sql")
└── 长期 memory、session memory、transcript、tool result
```

## 配置

默认使用 SQLite，运行示例不需要额外启动数据库：

```dotenv
SQL_URL=sqlite:///advanced-memory-sql-demo.db
SQL_IS_ASYNC=false
```

使用 MySQL 时：

```dotenv
SQL_URL=mysql+aiomysql://user:password@host:3306/trpc_agent_advanced_memory?charset=utf8mb4
SQL_IS_ASYNC=true
```

也可以通过 `MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_HOST`、`MYSQL_PORT` 和
`MYSQL_DB` 构造 MySQL URL。模型配置需要设置：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-base-url
TRPC_AGENT_MODEL_NAME=your-model-name
```

`M_TTL` 默认控制长期 memory 的过期时间，`SESSION_TTL` 控制 session 相关内容的过期时间，
单位都是秒。

更多 Advanced Memory 配置请参考[Advanced Memory README](../memory_service_with_advanced_memory/README.md)。

## 运行

```bash
source .venv/bin/activate
cd examples/memory_service_with_advanced_memory_sql
python run_agent.py
```

脚本会依次启动两个独立进程：

```text
RUNNER A PROCESS
├── 使用 7 条对话模拟记忆建立过程
└── Alice 的姓名和 favorite color 会被保存到长期 memory

RUNNER B PROCESS
├── 使用新的 session
├── 询问 Alice 的 name
└── 询问 Alice 的 favorite color
```

Runner B 应该能够回答：

```text
name: Alice
favorite color: blue
```

也可以单独运行：

```bash
python run_agent.py --phase write  # Runner A
python run_agent.py --phase read   # Runner B
```

第一次运行后，SQLite 文件 `advanced-memory-sql-demo.db` 会自动创建，
Advanced Memory 的表也会自动创建。

## 最基本的构建方式

SQL 版本最核心的构建过程可以简化为三步：

```python
sql_url = "mysql+aiomysql://user:password@localhost:3306/trpc_agent_advanced_memory"

memory_service = AdvancedMemoryService(
    AdvancedMemoryConfig(
        storage_backend="sql",
        sql_url=sql_url,
        sql_is_async=True,
        memory_ttl_seconds=120,  # from M_TTL; omit to disable expiration
        session_ttl_seconds=60,  # from SESSION_TTL; omit to disable expiration
    )
)

session_config = SessionServiceConfig(
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=60,  # same value as SESSION_TTL
        cleanup_interval_seconds=60,
    )
)
session_service = SqlSessionService(
    db_url=sql_url,
    is_async=True,
    session_config=session_config,
)

runner = Runner(
    app_name="advanced-memory-sql-demo",
    agent=create_agent(),
    session_service=session_service,
    memory_service=memory_service,
)
```

其中：

- 用户只需要配置 `M_TTL` 和 `SESSION_TTL` 两个 TTL；
- `AdvancedMemoryService` 负责长期 memory、session memory、transcript 和 tool result；
- `SqlSessionService` 负责框架 Session、app state 和 user state；
- `Runner` 将 Agent、Session Service 和 Memory Service 组合起来；
- 运行请求时通过 `user_id` 和 `session_id` 指定用户及会话；
- 多个节点只要使用相同的 SQL 数据库、`app_name` 和 `user_id`，就能访问同一份长期 memory。

## 运行结果（实测）

```text

==================== WRITE PROCESS ====================

----- Runner A, query 1 -----
📝 user: Do you remember my name?
🔧 tool call: list_memory_index({})
📊 Tool Result: {'index_path': '/data/workspace/trpc-agent-python-am-service/examples/memory_service_with_advanced_memory_sql/tenants/advanced-memory-sql-demo/sql-demo-user/MEMORY/MEMORY.md', 'index': ''}
🤖 Assistant: I checked my long-term memory index, and it's currently empty — I don't have any saved memories about you yet, so I don't remember your name.

If you'd like, tell me your name (or anything else you'd like me to remember about you), and I'll save it to my memory so I can remember it across future conversations.

----- Runner A, query 2 -----
📝 user: Do you remember my favorite color?
🔧 tool call: list_memory_index({})
📊 Tool Result: {'index_path': '/data/workspace/trpc-agent-python-am-service/examples/memory_service_with_advanced_memory_sql/tenants/advanced-memory-sql-demo/sql-demo-user/MEMORY/MEMORY.md', 'index': ''}
🤖 Assistant: I checked my long-term memory index, and it's currently empty — I don't have any saved memories yet, so I don't remember your favorite color.

If you tell me your favorite color (or any other preferences you'd like me to keep in mind), I can save it for future conversations!

----- Runner A, query 3 -----
📝 user: what is the weather like in paris?
🔧 tool call: get_weather_report({'city': 'Paris'})
📊 Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}
🤖 Assistant: The weather in Paris is **sunny** with a temperature of **25°C**. ☀️

......

==================== READ PROCESS ====================

----- Runner B, query 1 -----
📝 user: Do you remember my name?
🔧 tool call: list_memory_index({})
🔧 tool call: read_memory({'filename': 'user_identity.md'})
📊 Tool Result: {'index_path': '/data/workspace/trpc-agent-python-am-service/examples/memory_service_with_advanced_memory_sql/tenants/advanced-memory-sql-demo/sql-demo-user/MEMORY/MEMORY.md', 'index': '- [User identity]（user_identity.md）:Alice, favorite color blue.\n'}
📊 Tool Result: {'found': True, 'filename': 'user_identity.md', 'content': "---\nname: User identity\ndescription: The user's name and basic identity.\ntype: user\nupdated_at: 2026-09-08T07:26:18.283583+00:00\n---\n---\nname: User identity\ndescription: The user's name and basic identity.\ntype: user\nupdated_at: 2026-09-08T07:26:06.708029+00:00\n---\nThe user's name is Alice. She introduced herself in conversation. Her favorite color is blue.\n", 'updated_at': '2026-09-08T07:26:18.283583+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Assistant: Yes, I remember! Your name is **Alice** — from my long-term memory, that's how you introduced yourself. I also have noted that your favorite color is blue. 😊

If any of that has changed, just let me know and I'll update my memory records.

----- Runner B, query 2 -----
📝 user: Do you remember my favorite color?
🔧 tool call: read_memory({'filename': 'user_identity.md'})
📊 Tool Result: {'found': True, 'filename': 'user_identity.md', 'content': "---\nname: User identity\ndescription: The user's name and basic identity.\ntype: user\nupdated_at: 2026-09-08T07:26:18.283583+00:00\n---\n---\nname: User identity\ndescription: The user's name and basic identity.\ntype: user\nupdated_at: 2026-09-08T07:26:06.708029+00:00\n---\nThe user's name is Alice. She introduced herself in conversation. Her favorite color is blue.\n", 'updated_at': '2026-09-08T07:26:18.283583+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Assistant: Yes, I remember! Your favorite color is **blue**, Alice. 💙
```

## SQL 表

Advanced Memory 使用独立的表，不复用原始 `SqlMemoryService` 的 `mem_events`：

```text
advanced_memory_indexes
advanced_memory_topics
advanced_memory_session_memory
advanced_memory_transcripts
advanced_memory_transcript_seen
advanced_memory_tool_results
```

Markdown 内容保存在 `TEXT` 字段；transcript 保存 JSON 字符串；
`expires_at` 用于 SQL TTL。SQL 后端在读取时过滤过期数据，并在访问或写入时刷新
同一用户或同一 session 下相关记录的过期时间。
