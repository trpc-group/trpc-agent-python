# Redis SessionService + Session Compact

本示例只演示如何在已有 `RedisSessionService` 上增加：

- Tool Result Budget
- History Snip
- Microcompact
- AutoCompact
- AutoCompact 触发时生成的 Session Memory

压缩能力来自 `trpc_agent_sdk.sessions.compact`，长期记忆仍属于独立的
`trpc_agent_sdk.advanced_memory`。

## 组装关系

```text
AdvancedCompactConfig
        ↓ AdvancedSessionCompactManager
RedisSessionService
├── AdvancedSessionCompactManager
├── events: summary + recent Events
├── historical_events: 被压缩的原始 Events
└── state["_trpc_agent:summary"]

```

核心调用：

```python
session_config = SessionServiceConfig(
    store_historical_events=True,
)
compact_config = AdvancedCompactConfig(
    model_context_window_tokens=4096,
    token_autocompact_ratio=0.30,
)
session_service = RedisSessionService(
    db_url=redis_url,
    is_async=True,
    session_config=session_config,
    session_compact_manager=AdvancedSessionCompactManager(config=compact_config),
)

runner = Runner(
    app_name=app_name,
    agent=agent,
    session_service=session_service,
)
```

`RedisSessionService` 会接收 `session_compact_manager`。Compact 只使用 SessionService 的
`events`、`historical_events` 和 `state`，不创建额外的 Redis 存储。

## 兼容已有 Session

旧数据不需要包含 `_trpc_agent:summary`：

```python
summary = session.state.get("_trpc_agent:summary")
```

不存在时正常返回 `None`。只有上下文达到 AutoCompact 阈值后，子 Agent 才会
根据当前可读 Events 生成第一份 Summary。

前三个阶段只修改发给模型的 `LlmRequest`。AutoCompact 成功后还会把同一份
Session Memory 作为 summary Event 写到 `session.events[0]`，并把被替换的
原始 Events 移入 `session.historical_events`。因此下一轮直接读取
`summary + recent events`，无需重新加载已经压缩的活跃 Events。

## 配置与运行

复制并修改 `.env`：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-model-base-url
TRPC_AGENT_MODEL_NAME=your-model-name
REDIS_USER=
REDIS_PASSWORD=
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
SESSION_ID=simple-demo
```

运行：

```bash
cd examples/session_service_with_advanced_memory_redis
source ../../.venv/bin/activate
python run_agent.py
```

脚本默认使用 `simple-demo`，可通过 `SESSION_ID` 修改。重复运行可以验证
活跃窗口、历史原始 Events 和 Session Memory 都能跨进程恢复。

运行结束会输出 `Active Events`、`Historical Events`、活跃窗口是否以 summary
开头，以及 Session Memory state 是否存在，方便直接确认压缩是否触发。

## 存储职责

- `RedisSessionService`：Session、活跃 Events、historical Events 和 state。
- Compact 不创建独立的 Redis transcript、Tool Result 或 session-memory 存储。
