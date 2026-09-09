# Advanced Memory Compression Check

这个例子专门验证 Redis 和 SQL 后端的：

- Session Memory 子 Agent 摘要；
- AutoCompact；
- HistorySnip；
- Microcompact；
- Tool Result Budget 的 transcript 持久化。

示例把触发阈值调得很低，并让 Agent 每轮调用一次大结果工具。运行结束后会读取后端中的 transcript，打印实际写入的记录类型。

## Redis

先确保 Redis 已启动，并准备好模型环境变量：

```bash
export TRPC_AGENT_API_KEY='your-api-key'
export TRPC_AGENT_BASE_URL='https://your-endpoint/v1'
export TRPC_AGENT_MODEL_NAME='your-model'
export REDIS_URL='redis://127.0.0.1:6379/0'
```

运行：

```bash
cd trpc-agent-python-am-service
python3.12 examples/advanced_memory_compression_check/run_agent.py --backend redis
```

## SQL

SQLite 测试：

```bash
export SQL_URL='sqlite:///./advanced_memory_compression_check.db'
export SQL_IS_ASYNC=false
python3.12 examples/advanced_memory_compression_check/run_agent.py --backend sql
```

MySQL 测试时，将 `SQL_URL` 改成现有的 `mysql+aiomysql://...` 地址，并设置：

```bash
export SQL_IS_ASYNC=true
```

## 如何判断成功

重点查看结尾的 `Transcript record counts` 和 `Session memory written`。

正常情况下至少应该看到：

```text
session-memory-checkpoint
```

如果上下文压缩被触发，还会看到：

```text
autocompact-success
history-snip
microcompact-clear
tool-result-replacement
```

具体出现哪些记录取决于模型是否按要求调用工具，以及每轮生成的上下文大小。`Session memory written: yes` 表示摘要已经写入当前 Redis 或 SQL 后端。
