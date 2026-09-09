# Advanced Memory Redis 示例

本示例演示如何将 Advanced Memory 的本地文件存储切换为 Redis，并验证：

- Redis：`RedisSessionService` + `AdvancedMemoryService`
- 长期 memory 可以跨 Python 进程持久化；
- 同一用户在不同 `session_id` 中可以读取自己的长期 memory；
- session 相关数据和长期 memory 可以分别设置 TTL；
- Redis 中的 Markdown、Stream 和索引数据如何组织。

示例使用两个服务：

```text
RedisSessionService
└── 保存 Session、app state、user state

AdvancedMemoryService(storage_backend="redis")
└── 保存长期 memory、session memory、transcript、tool result
```

## 环境要求

- Python 3.10+，推荐 Python 3.12；
- 可访问的 Redis 服务；
- 可正常调用的模型服务。

如果还没有 Redis，可以使用 Docker：

```bash
docker run --name advanced-memory-redis \
  -p 6379:6379 \
  -d redis:7-alpine
```

容器已创建过时不要重复执行 `docker run`，直接启动：

```bash
docker start advanced-memory-redis
```

检查 Redis：

```bash
docker exec advanced-memory-redis redis-cli PING
# PONG
```

## Redis 配置方式

### 方式一：使用完整连接串

在当前目录的 `.env` 中配置：

```dotenv
REDIS_URL=redis://localhost:6379/0
```

带密码：

```dotenv
REDIS_URL=redis://:password@redis.example.com:6379/0
```

Redis ACL 用户名和密码：

```dotenv
REDIS_URL=redis://username:password@redis.example.com:6379/0
```

启用 TLS：

```dotenv
REDIS_URL=rediss://:password@redis.example.com:6380/0
```

密码包含 `@`、`:`、`/`、`#` 等特殊字符时，需要进行 URL 编码。

### 方式二：分别配置连接参数

也可以不设置 `REDIS_URL`，改为：

```dotenv
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_USER=
REDIS_PASSWORD=
REDIS_TLS=false
```

云 Redis 使用示例：

```dotenv
REDIS_HOST=your-redis.example.com
REDIS_PORT=6379
REDIS_DB=0
REDIS_USER=your-user
REDIS_PASSWORD=your-password
REDIS_TLS=true
```

代码会优先使用 `REDIS_URL`；未设置时才根据上述字段构造连接串。

## 模型和 TTL 配置

`.env` 示例：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-model-base-url
TRPC_AGENT_MODEL_NAME=your-model-name

REDIS_URL=redis://localhost:6379/0

# 长期 memory 的 TTL，单位为秒
M_TTL=120

# 所有 session 相关内容的 TTL，单位为秒
SESSION_TTL=60
```

TTL 规则：

- `M_TTL` 管理用户级长期 memory 的全部 Redis key；
- `SESSION_TTL` 管理 session memory、transcript、tool result、去重 key；
- `SESSION_TTL` 也传给 `RedisSessionService`，用于 Session 和 state；
- TTL 会在访问或写入时刷新，是“最后一次活动后过期”；
- 两个 TTL 必须设置为大于 0 的整数。

更多 Advanced Memory 配置请参考[Advanced Memory README](../memory_service_with_advanced_memory/README.md)。

## 运行示例

```bash
cd examples/memory_service_with_advanced_memory_redis
source ../../.venv/bin/activate
python run_agent.py
```

脚本会自动启动两个独立的 Python 子进程：

```text
RUNNER A PROCESS
├── 使用 7 条对话模拟记忆建立过程
└── Alice 的姓名和 favorite color 会被保存到长期 memory

RUNNER B PROCESS
├── 使用新的 session
├── 询问 Alice 的 name
└── 询问 Alice 的 favorite color
```

两个进程使用相同的：

```text
app_name = advanced-memory-redis-demo
user_id  = redis-demo-user
```

但使用不同的 `session_id`。第二个进程应该能够回答：

```text
name: Alice
favorite color: blue
```

这证明了 Redis 数据可以跨进程、跨 session 持久化。

也可以单独运行某个阶段：

```bash
python run_agent.py --phase write  # Runner A
python run_agent.py --phase read   # Runner B
```

## 最基本的构建方式

Redis 版本最核心的构建过程可以简化为三步：

```python
redis_url = "redis://:password@localhost:6379/0"

memory_service = AdvancedMemoryService(
    AdvancedMemoryConfig(
        storage_backend="redis",
        redis_url=redis_url,
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
session_service = RedisSessionService(
    db_url=redis_url,
    is_async=True,
    session_config=session_config,
)

runner = Runner(
    app_name="advanced-memory-redis-demo",
    agent=create_agent(),
    session_service=session_service,
    memory_service=memory_service,
)
```

其中：

- 用户只需要配置 `M_TTL` 和 `SESSION_TTL` 两个 TTL；
- `AdvancedMemoryService` 负责长期 memory、session memory、transcript 和 tool result；
- `RedisSessionService` 负责框架 Session、app state 和 user state；
- `Runner` 将 Agent、Session Service 和 Memory Service 组合起来；
- 运行请求时通过 `user_id` 和 `session_id` 指定用户及会话。

## 运行结果（实测）

```text
 user: Do you remember my name?
🔧 tool call: list_memory_index({})
📊 Tool Result: {'index_path': 'advanced-memory://redis/advanced-memory-redis-demo:v1:{advanced-memory-redis-demo:redis-demo-user}:memory:index', 'index': ''}
🤖 Assistant: I checked my long-term memory, but I'm afraid I don't have anything saved yet — the memory index is currently empty, so I don't know your name. 

If you'd like, just tell me your name (and anything else you'd like me to remember about you), and I'll save it so I can recall it in future conversations!

----- Runner A, query 2 -----

📝 user: Do you remember my favorite color?
🔧 tool call: list_memory_index({})
📊 Tool Result: {'index_path': 'advanced-memory://redis/advanced-memory-redis-demo:v1:{advanced-memory-redis-demo:redis-demo-user}:memory:index', 'index': ''}
🤖 Assistant: I checked my long-term memory, but I don't have anything saved about your favorite color yet — my memory index is currently empty.

If you'd like, tell me your favorite color and I'll remember it for future conversations. 💬

----- Runner A, query 3 -----

📝 user: what is the weather like in paris?
🔧 tool call: get_weather_report({'city': 'Paris'})
📊 Tool Result: {'status': 'success', 'report': 'The weather in Paris is sunny with a temperature of 25 degrees Celsius.'}
🤖 Assistant: The weather in Paris is **sunny** with a temperature of **25°C**. ☀️

......

==================== READ PROCESS ====================

----- Runner B, query 1 -----

📝 user: Do you remember my name?
🔧 tool call: read_memory({'filename': 'alice-identity.md'})
📊 Tool Result: {'found': True, 'filename': 'alice-identity.md', 'content': "---\nname: Alice's identity\ndescription: User's name and personal preferences.\ntype: user\nupdated_at: 2026-09-08T07:17:40.559641+00:00\n---\n- Name: Alice\n- Favorite color: blue\n", 'updated_at': '2026-09-08T07:17:40.559641+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Assistant: Yes, I do — your name is Alice! 😊 And I also remember that your favorite color is blue.

----- Runner B, query 2 -----

📝 user: Do you remember my favorite color?
🔧 tool call: read_memory({'filename': 'alice-identity.md'})
📊 Tool Result: {'found': True, 'filename': 'alice-identity.md', 'content': "---\nname: Alice's identity\ndescription: User's name and personal preferences.\ntype: user\nupdated_at: 2026-09-08T07:17:40.559641+00:00\n---\n- Name: Alice\n- Favorite color: blue\n", 'updated_at': '2026-09-08T07:17:40.559641+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Assistant: Yes! According to your memory profile, your favorite color is **blue**. 💙
```

## 查看 Redis 中的数据

进入 Redis CLI：

```bash
docker exec -it advanced-memory-redis redis-cli
```

查看本示例写入的全部 Redis key：

```redis
SCAN 0 MATCH advanced-memory-redis-demo:v1:* COUNT 100
```

也可以在命令行中直接查看全部 key：

```bash
docker exec advanced-memory-redis redis-cli --scan \
  --pattern 'advanced-memory-redis-demo:v1:*'
```

`SCAN` 不会像 `KEYS *` 一样阻塞 Redis，适合共享或云 Redis 环境。

## 查看 TTL

长期 memory：

```redis
TTL "advanced-memory-redis-demo:v1:{advanced-memory-redis-demo:redis-demo-user}:memory:index"
TTL "advanced-memory-redis-demo:v1:{advanced-memory-redis-demo:redis-demo-user}:memory:topic:user_favorite_project_code.md"
```

预期接近 `120`。

session transcript：

```redis
TTL "advanced-memory-redis-demo:v1:{advanced-memory-redis-demo:redis-demo-user:redis-write-session}:transcript"
```

预期接近 `60`。

TTL 含义：

```text
-1  永不过期
-2  key 不存在或已经过期
大于 0  剩余秒数
```

观察 session key：

```bash
docker exec advanced-memory-redis redis-cli --scan \
  --pattern 'advanced-memory-redis-demo:v1:*:summary'

docker exec advanced-memory-redis redis-cli --scan \
  --pattern 'advanced-memory-redis-demo:v1:*:transcript*'
```

## 清理测试数据

只删除本示例的 Advanced Memory key：

```bash
docker exec advanced-memory-redis redis-cli --scan \
  --pattern 'advanced-memory-redis-demo:v1:*' \
  | xargs -r docker exec -i advanced-memory-redis redis-cli DEL
```

测试 Redis 独占一个数据库时，也可以清空当前数据库：

```bash
docker exec -it advanced-memory-redis redis-cli FLUSHDB
```

`FLUSHDB` 会删除当前 Redis DB 中的所有数据，不要在共享或生产数据库执行。

## Redis 中的存储形式

### 长期 memory

本地文件概念：

```text
MEMORY/MEMORY.md
MEMORY/user_favorite_project_code.md
```

Redis 映射：

```text
{prefix}:{app:user}:memory:index
{prefix}:{app:user}:memory:topic:user_favorite_project_code.md
```

类型都是 Redis String，内容是 Markdown。

topic 列表的辅助索引：

```text
{prefix}:{app:user}:memory:topics
```

类型是 ZSet，member 是 topic 文件名，score 是更新时间。

memory TTL registry：

```text
{prefix}:{app:user}:memory:keys
```

它记录该用户的所有长期 memory key，用于统一刷新 `M_TTL`。

### session memory

本地文件概念：

```text
SESSION/{session_id}/session_memory.md
```

Redis 映射：

```text
{prefix}:{app:user:session}:summary
```

类型是 Redis String，内容是 Markdown。

### transcript

本地文件概念：

```text
SESSION/{session_id}/transcript.jsonl
```

Redis 映射：

```text
{prefix}:{app:user:session}:transcript
```

类型是 Redis Stream，每条记录保存一份 JSON 数据。

### transcript 去重和 tool result

```text
{prefix}:{app:user:session}:transcript:seen:{unique_key}
{prefix}:{app:user:session}:tool:{result_id}
```

去重 key 使用 Set，tool result 使用 String。

session TTL registry：

```text
{prefix}:{app:user:session}:keys
```

它记录该 session 下的 summary、transcript、tool result 等 key，用于统一刷新
`SESSION_TTL`，避免同一个 session 的不同内容出现 TTL 不一致。
