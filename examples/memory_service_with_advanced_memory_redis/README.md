# Advanced Memory Redis 持久化示例

本示例演示如何使用 `AdvancedMemoryService` 将长期记忆保存到 Redis，实现跨会话、跨 Python 进程的持久化记忆。

## 关键特性

- **主动式记忆**：Agent 根据对话内容主动调用工具保存长期有效的信息。
- **记忆分类**：每条记忆包含名称、描述、类型、摘要和详细内容。
- **基于记忆索引的记忆召回**：先读取 Redis 中的 `MEMORY.md` 索引，
  再读取与问题相关的记忆内容。
- **Redis 持久化**：多个进程或实例使用相同的 Redis、应用名和用户 ID时，可以访问同一份长期记忆。

## Agent 层级结构说明

`AdvancedMemoryService` 通过 `Runner` 绑定到 Agent，并根据配置使用 Redis 保存记忆索引和记忆主题。Agent 通过三个工具主动管理长期记忆。

## 关键代码解释

### `save_memory`

保存或更新一条长期记忆，同时更新 Redis 中的记忆索引。

### `list_memory_index`

读取当前用户的记忆索引，帮助 Agent 找到与当前问题相关的记忆文件。

### `read_memory`

根据索引中的文件名读取完整记忆内容。

## 环境要求

- Python 3.10 或更高版本
- 可访问的 Redis 服务
- 一个可访问的 OpenAI 兼容模型服务

**启动本地 Redis：**

```bash
docker run --name advanced-memory-redis \
  -p 6379:6379 \
  -d redis:7-alpine
```

然后在当前目录的 `.env` 中配置：

```dotenv
REDIS_URL=redis://localhost:6379/0
```

如果容器已经存在，执行：

```bash
docker start advanced-memory-redis
```

检查 Redis：

```bash
docker exec advanced-memory-redis redis-cli PING
# PONG
```

如果使用已有的**远程 Redis 服务**，不需要执行 Docker 命令，只需要在当前目录的`.env` 中配置 Redis 连接信息：

```dotenv
REDIS_URL=redis://:password@redis.example.com:6379/0
```

如果 Redis 使用 ACL 用户名和密码：

```dotenv
REDIS_URL=redis://username:password@redis.example.com:6379/0
```

启用 TLS 时使用 `rediss` 协议：

```dotenv
REDIS_URL=rediss://username:password@redis.example.com:6380/0
```

也可以拆分配置：

```dotenv
REDIS_HOST=redis.example.com
REDIS_PORT=6379
REDIS_DB=0
REDIS_USER=your-user
REDIS_PASSWORD=your-password
REDIS_TLS=false
```

代码会优先使用 `REDIS_URL`；未设置时，才会根据这些字段构造连接串。密码包含 `@`、`:`、`/`、`#` 等特殊字符时，需要进行 URL 编码。

## 模型配置

在当前目录的 `.env` 中配置：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://your-llm-endpoint/v1
TRPC_AGENT_MODEL_NAME=your-model-name
```

Redis 配置请参考上面的本地 Redis 或远程 Redis 配置方式。

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
cd examples/memory_service_with_advanced_memory_redis
source ../../.venv/bin/activate
python run_agent.py
```

脚本会依次启动写入和读取两个独立进程，验证 Redis 中的记忆可以跨进程和不同会话读取。也可以单独运行某个阶段：

```bash
python run_agent.py --phase write
python run_agent.py --phase read
```

## Redis 中的存储

记忆索引和主题内容会以 Redis key 保存，key 前缀为：

```text
advanced-memory-redis-demo:v1:*
```

查看本示例写入的 key：

```bash
docker exec advanced-memory-redis redis-cli --scan \
  --pattern 'advanced-memory-redis-demo:v1:*'
```

示例中的记忆 TTL 在代码的 `AdvancedMemoryServiceConfig` 中配置为 `memory_ttl_seconds=120`。

## 运行结果（实测）

```txt
==================== WRITE PROCESS ====================

----- Runner A, query 1 -----

📝 user: Do you remember my name?
🤖 Assistant:


🔧 tool call: list_memory_index({})
📊 Tool Result: {'index_path': 'advanced-memory://redis/advanced-memory-redis-demo:v1:{advanced-memory-redis-demo:redis-demo-user}:memory:MEMORY.md', 'index': ''}
🤖 Assistant: I checked my long-term memory, but it looks like I don't have any record of your name yet — my memory index is currently empty.

If you'd like, tell me your name (or anything else you'd like me to remember about you), and I'll save it for future conversations. 😊

......

==================== READ PROCESS ====================

----- Runner B, query 1 -----

📝 user: Do you remember my name?
🔧 tool call: read_memory({'filename': 'user-identity.md'})
📊 Tool Result: {'found': True, 'filename': 'user-identity.md', 'content': "---\nname: User identity\ndescription: Alice's name and basic identity for personalization.\ntype: user\nupdated_at: 2026-09-11T05:54:20.441889+00:00\n---\nThe user's name is Alice. She introduced herself on first contact. Use this name for personalized responses.\n", 'updated_at': '2026-09-11T05:54:20.441889+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Assistant: Yes, I remember! Your name is **Alice**. 😊

I've stored that in my long-term memory so I can personalize my responses for you. Is there anything else I can help you with?

----- Runner B, query 2 -----

📝 user: Do you remember my favorite color?
🔧 tool call: read_memory({'filename': 'favorite-color.md'})
📊 Tool Result: {'found': True, 'filename': 'favorite-color.md', 'content': "---\nname: Favorite color\ndescription: Alice's favorite color.\ntype: user\nupdated_at: 2026-09-11T05:54:24.620584+00:00\n---\nAlice's favorite color is blue.\n", 'updated_at': '2026-09-11T05:54:24.620584+00:00', 'freshness': 'today', 'freshness_notice': 'This memory was last updated today. It is a point-in-time observation and may no longer reflect the current state. Verify it when necessary, and update this memory if it is outdated or incorrect.'}
🤖 Assistant: Yes, I remember! Your favorite color is **blue**. 💙
```